"""
PostgradWatcher — 异步研究生监控器

监控两类变化并发布事件：
    1. 进程退出 → postgrad_exit 事件
    2. journal.json 文件变化 → new_node 事件

用法:
    watcher = PostgradWatcher(group.context, group.event_bus)
    watcher.register(postgrad_name, pid, journal_path)
    # watcher 随 group.run() 的 asyncio 循环运行
"""
import os
import json
import asyncio
from pathlib import Path

from agents.event import Event, EventBus
from agents.context import SharedContext, PostgradState


class PostgradWatcher:
    def __init__(self, context: SharedContext, event_bus: EventBus):
        self.ctx = context
        self.event_bus = event_bus
        self._tasks: dict[str, asyncio.Task] = {}
        self._node_counts: dict[str, int] = {}

    async def watch(self, postgrad_name: str):
        state = self.ctx.postgrads.get(postgrad_name)
        if not state:
            return

        journal_dir = self._find_journal_dir(postgrad_name)
        if journal_dir:
            state.journal_path = str(journal_dir / "journal.json")

        task = asyncio.create_task(self._watch_loop(postgrad_name))
        self._tasks[postgrad_name] = task

    async def unwatch(self, postgrad_name: str):
        task = self._tasks.pop(postgrad_name, None)
        if task:
            task.cancel()

    async def _watch_loop(self, postgrad_name: str):
        state = self.ctx.postgrads.get(postgrad_name)
        if not state:
            return

        print(f"[watcher] started watching {postgrad_name}")
        while True:
            await asyncio.sleep(self.ctx.config.get("check_interval", 30))

            alive = False
            if state.process_pid:
                try:
                    os.kill(state.process_pid, 0)
                    alive = True
                except ProcessLookupError:
                    pass

            print(f"[watcher] {postgrad_name}: alive={alive}, pid={state.process_pid}")

            if not alive and state.process_pid:
                print(f"[watcher] {postgrad_name} exited, publishing postgrad_exit")
                await self.event_bus.publish(Event("postgrad_exit", {
                    "name": postgrad_name,
                    "exit_code": -1,
                    "has_good_nodes": self._count_good_nodes(state) > 0,
                }))
                state.process_pid = 0
                return

            node_count = self._count_nodes(state)
            prev_count = self._node_counts.get(postgrad_name, 0)

            print(f"[watcher] {postgrad_name}: node_count={node_count}, prev_count={prev_count}")

            if node_count > prev_count:
                print(f"[watcher] {postgrad_name} has new nodes, publishing new_node")
                await self.event_bus.publish(Event("new_node", {
                    "name": postgrad_name,
                    "node_count": node_count,
                    "new_nodes": node_count - prev_count,
                    "good_nodes": self._count_good_nodes(state),
                }))
                state.last_node_count = node_count
                state.last_progress_time = __import__("time").time()
                state.stuck_count = 0
                self._node_counts[postgrad_name] = node_count
            else:
                state.stuck_count += 1
                print(f"[watcher] {postgrad_name}: stuck_count={state.stuck_count}")
                if state.stuck_count >= 8:
                    print(f"[watcher] {postgrad_name} is stuck, publishing postgrad_stuck")
                    await self.event_bus.publish(Event("postgrad_stuck", {
                        "name": postgrad_name,
                        "stuck_duration": state.stuck_count * self.ctx.config.get("check_interval", 30),
                        "node_count": node_count,
                    }))
                    state.stuck_count = 0

    def _find_journal_dir(self, postgrad_name: str) -> Path | None:
        keyword = postgrad_name.replace("postgrad_", "").replace("_agent", "")
        exp_dir = self.ctx.root / "experiments"
        if not exp_dir.exists():
            return None
        candidates = sorted(
            [d for d in exp_dir.iterdir() if d.is_dir() and keyword in d.name],
            key=lambda d: d.stat().st_mtime,
        )
        if not candidates:
            return None
        for run_dir in candidates[-1].iterdir():
            if run_dir.is_dir() and run_dir.name.startswith("run_"):
                return run_dir
        return candidates[-1]

    def _count_nodes(self, state: PostgradState) -> int:
        if not state.journal_path or not Path(state.journal_path).exists():
            return 0
        try:
            with open(state.journal_path) as f:
                data = json.load(f)
            nodes = data if isinstance(data, list) else data.get("nodes", [])
            return len(nodes)
        except Exception:
            return 0

    def _count_good_nodes(self, state: PostgradState) -> int:
        if not state.journal_path or not Path(state.journal_path).exists():
            return 0
        try:
            with open(state.journal_path) as f:
                data = json.load(f)
            nodes = data if isinstance(data, list) else data.get("nodes", [])
            return sum(1 for n in nodes if not n.get("is_buggy"))
        except Exception:
            return 0
