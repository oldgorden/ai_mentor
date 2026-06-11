"""
研究生进程管理工具：check_status / create / restart / kill

这些工具管理研究生的实验进程（启动/重启/终止），属于"脏活累活"。
通常由导师决策调用，或由研究生自己管理自己的进程。
"""
import os
import sys
import json
import asyncio
from pathlib import Path
from typing import Optional

from agents.tool import Tool, ToolResult
from agents.context import SharedContext, PostgradState


class CheckPostgradStatus(Tool):
    name = "check_postgrad_status"
    description = "检查指定研究生的状态：进程是否存活、实验节点数量、最新日志"
    parameters = {
        "type": "object",
        "properties": {
            "postgrad_name": {"type": "string", "description": "研究生名称"},
        },
        "required": ["postgrad_name"],
    }
    permission = "postgrad:read"
    confidence_required = 0.0

    async def execute(self, ctx: SharedContext, *, postgrad_name: str) -> ToolResult:
        state = ctx.postgrads.get(postgrad_name)
        if not state:
            return ToolResult(success=False, error=f"Postgrad not found: {postgrad_name}")

        alive = False
        if state.process_pid:
            try:
                os.kill(state.process_pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False

        node_count = state.last_node_count
        good_nodes = 0
        if state.journal_path and Path(state.journal_path).exists():
            try:
                with open(state.journal_path) as f:
                    data = json.load(f)
                nodes = data if isinstance(data, list) else data.get("nodes", [])
                node_count = len(nodes)
                good_nodes = sum(1 for n in nodes if not n.get("is_buggy"))
                state.last_node_count = node_count
            except Exception:
                pass

        log_tail = ""
        if state.log_path and Path(state.log_path).exists():
            try:
                with open(state.log_path) as f:
                    lines = f.readlines()
                log_tail = "".join(lines[-5:])
            except Exception:
                pass

        return ToolResult(success=True, data={
            "name": postgrad_name,
            "alive": alive,
            "pid": state.process_pid,
            "node_count": node_count,
            "good_nodes": good_nodes,
            "stuck_count": state.stuck_count,
            "restart_count": state.restart_count,
            "log_tail": log_tail,
        })


class CreatePostgrad(Tool):
    name = "create_postgrad"
    description = "创建一个新研究生实验进程，分配研究方向"
    parameters = {
        "type": "object",
        "properties": {
            "idea_idx": {"type": "integer", "description": "研究想法索引"},
            "postgrad_name": {"type": "string", "description": "研究生名称（可选）"},
        },
        "required": ["idea_idx"],
    }
    permission = "postgrad:write"
    confidence_required = 0.8

    async def execute(self, ctx: SharedContext, *, idea_idx: int,
                      postgrad_name: str = None) -> ToolResult:
        max_postgrads = ctx.config.get("postgrad_config", {}).get("max_postgrads", 3)
        if len(ctx.postgrads) >= max_postgrads:
            return ToolResult(success=False, error=f"已达最大研究生数 {max_postgrads}")

        if idea_idx >= len(ctx.ideas):
            return ToolResult(success=False, error=f"想法索引越界: {idea_idx}")

        idea = ctx.ideas[idea_idx]
        if not postgrad_name:
            postgrad_name = f"postgrad_{idea.get('Name', idea_idx)}"

        for existing_name, s in ctx.postgrads.items():
            if existing_name == postgrad_name:
                return ToolResult(success=False, error=f"研究生已存在: {postgrad_name}")

        model = ctx.config.get("postgrad_config", {}).get("model", "custom/mimo-v2.5-pro")
        ideas_path = ctx.config.get("ideas_file", "")

        env = os.environ.copy()
        for k in ["ALL_PROXY", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
            env.pop(k, None)
        env["AI_SCIENTIST_ROOT"] = str(ctx.root)

        cmd = [
            sys.executable,
            str(ctx.root / "launch_scientist_bfts.py"),
            "--load_ideas", str(ctx.root / ideas_path),
            "--idea_idx", str(idea_idx),
            "--model_writeup", model,
            "--model_citation", model,
            "--model_review", model,
            "--model_agg_plots", model,
            "--model_writeup_small", model,
            "--skip_review",
        ]

        log_dir = ctx.root / "mentor" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = str(log_dir / f"{postgrad_name}.log")

        with open(log_path, "w") as log_f:
            proc = await asyncio.create_subprocess_exec(
                *cmd, env=env, cwd=str(ctx.root),
                stdout=log_f, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )

        state = PostgradState(
            name=postgrad_name,
            idea_idx=idea_idx,
            process_pid=proc.pid,
            last_progress_time=__import__("time").time(),
            log_path=log_path,
        )
        ctx.postgrads[postgrad_name] = state

        watcher = ctx.get_watcher()
        print(f"[create_postgrad] watcher: {watcher}")
        if watcher:
            print(f"[create_postgrad] starting watch for {postgrad_name}")
            asyncio.create_task(watcher.watch(postgrad_name))

        return ToolResult(success=True, data={
            "name": postgrad_name,
            "pid": proc.pid,
            "idea": idea.get("Name", ""),
        })


class RestartPostgrad(Tool):
    name = "restart_postgrad"
    description = "重启研究生实验进程。自动使用 --continue_from 继续旧实验"
    parameters = {
        "type": "object",
        "properties": {
            "postgrad_name": {"type": "string"},
            "guidance": {"type": "string", "description": "注入给研究生的指导文本"},
        },
        "required": ["postgrad_name"],
    }
    permission = "postgrad:write"
    confidence_required = 0.7

    async def execute(self, ctx: SharedContext, *, postgrad_name: str,
                      guidance: str = "") -> ToolResult:
        state = ctx.postgrads.get(postgrad_name)
        if not state:
            return ToolResult(success=False, error=f"Postgrad not found: {postgrad_name}")

        if state.process_pid:
            try:
                import signal
                os.killpg(os.getpgid(state.process_pid), signal.SIGTERM)
            except Exception:
                pass
            await asyncio.sleep(3)
            try:
                os.killpg(os.getpgid(state.process_pid), signal.SIGKILL)
            except Exception:
                pass

        model = ctx.config.get("postgrad_config", {}).get("model", "custom/mimo-v2.5-pro")
        ideas_path = ctx.config.get("ideas_file", "")
        idea_idx = state.idea_idx

        keyword = postgrad_name.replace("postgrad_", "").replace("_agent", "")
        exp_dir = ctx.root / "experiments"
        continue_from = None
        if exp_dir.exists():
            candidates = sorted(
                [d for d in exp_dir.iterdir() if d.is_dir() and keyword in d.name],
                key=lambda d: d.stat().st_mtime,
            )
            if candidates:
                continue_from = str(candidates[-1])

        env = os.environ.copy()
        for k in ["ALL_PROXY", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
            env.pop(k, None)
        env["AI_SCIENTIST_ROOT"] = str(ctx.root)

        cmd = [
            sys.executable,
            str(ctx.root / "launch_scientist_bfts.py"),
            "--load_ideas", str(ctx.root / ideas_path),
            "--idea_idx", str(idea_idx),
            "--model_writeup", model,
            "--model_citation", model,
            "--model_review", model,
            "--model_agg_plots", model,
            "--model_writeup_small", model,
            "--skip_review",
        ]
        if continue_from:
            cmd.extend(["--continue_from", continue_from])

        if guidance:
            idea_path = ctx.root / ideas_path
            try:
                with open(idea_path) as f:
                    ideas_data = json.load(f)
                ideas_list = ideas_data if isinstance(ideas_data, list) else [ideas_data]
                if idea_idx < len(ideas_list):
                    ideas_list[idea_idx]["_mentor_guidance"] = guidance
                    with open(idea_path, "w") as f:
                        json.dump(ideas_data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

        with open(state.log_path, "w") as log_f:
            proc = await asyncio.create_subprocess_exec(
                *cmd, env=env, cwd=str(ctx.root),
                stdout=log_f, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )

        state.process_pid = proc.pid
        state.stuck_count = 0
        state.restart_count += 1
        state.last_progress_time = __import__("time").time()

        return ToolResult(success=True, data={
            "name": postgrad_name,
            "pid": proc.pid,
            "continue_from": continue_from,
            "restart_count": state.restart_count,
        })


class KillPostgrad(Tool):
    name = "kill_postgrad"
    description = "终止研究生实验进程"
    parameters = {
        "type": "object",
        "properties": {
            "postgrad_name": {"type": "string"},
        },
        "required": ["postgrad_name"],
    }
    permission = "postgrad:write"
    confidence_required = 0.9

    async def execute(self, ctx: SharedContext, *, postgrad_name: str) -> ToolResult:
        state = ctx.postgrads.get(postgrad_name)
        if not state:
            return ToolResult(success=False, error=f"Postgrad not found: {postgrad_name}")

        if state.process_pid:
            try:
                import signal
                try:
                    os.killpg(os.getpgid(state.process_pid), signal.SIGTERM)
                except Exception:
                    os.killpg(os.getpgid(state.process_pid), signal.SIGKILL)
            except Exception as e:
                return ToolResult(success=False, error=str(e))

        state.process_pid = 0
        return ToolResult(success=True, data={"name": postgrad_name})
