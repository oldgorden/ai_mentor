"""
TaskQueue — 任务队列系统

chief_mentor 不亲自动手，通过任务队列把工作分配给其他成员。

流程:
    chief 分配 task → member 取 task → 执行 → 结果回传 → chief 决定下一步

任务类型:
    code_task      → code_mentor       写/修改实验代码
    run_task       → researcher        执行实验
    review_task    → reasoning_mentor  审查结果合理性
    literature_task → multimodal_mentor 搜论文
    writeup_task   → writer_postgrad   写论文
    review_paper   → reviewer_postgrad 审稿

每个成员有一个 asyncio.Queue，chief 往里放 Task，
成员的 work_loop 从队列取任务、执行、把结果放回 chief 的队列。
"""
import asyncio
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Coroutine


@dataclass
class Task:
    task_id: str = ""
    task_type: str = ""
    description: str = ""
    created_by: str = ""
    created_at: float = 0.0
    params: dict = field(default_factory=dict)
    priority: int = 0
    experiment_dir: str = ""

    def __post_init__(self):
        if not self.task_id:
            self.task_id = f"{self.task_type}_{int(time.time()*1000)}"
        if not self.created_at:
            self.created_at = time.time()


@dataclass
class TaskResult:
    task_id: str = ""
    task_type: str = ""
    completed_by: str = ""
    completed_at: float = 0.0
    success: bool = False
    data: Any = None
    error: str = ""
    thought: str = ""

    def __post_init__(self):
        if not self.completed_at:
            self.completed_at = time.time()


class TaskQueue:
    def __init__(self):
        self._queues: dict[str, asyncio.Queue] = {}
        self._results: dict[str, list[TaskResult]] = {}
        self._pending: dict[str, Task] = {}

    def get_queue(self, member_name: str) -> asyncio.Queue:
        if member_name not in self._queues:
            self._queues[member_name] = asyncio.Queue()
        return self._queues[member_name]

    async def submit(self, task: Task, assign_to: str):
        self._pending[task.task_id] = task
        await self.get_queue(assign_to).put(task)

    async def next_task(self, member_name: str, timeout: float = None) -> Task | None:
        try:
            if timeout:
                return await asyncio.wait_for(
                    self.get_queue(member_name).get(), timeout=timeout
                )
            return await self.get_queue(member_name).get()
        except asyncio.TimeoutError:
            return None

    def submit_result(self, result: TaskResult):
        self._results.setdefault(result.completed_by, []).append(result)
        if result.task_id in self._pending:
            del self._pending[result.task_id]

    def get_results(self, member_name: str = None, n: int = 10) -> list[TaskResult]:
        if member_name:
            return self._results.get(member_name, [])[-n:]
        all_results = []
        for results in self._results.values():
            all_results.extend(results)
        all_results.sort(key=lambda r: r.completed_at, reverse=True)
        return all_results[:n]

    @property
    def pending_count(self) -> int:
        return sum(q.qsize() for q in self._queues.values())

    def status(self) -> dict:
        return {
            "pending": {name: q.qsize() for name, q in self._queues.items() if q.qsize() > 0},
            "total_completed": sum(len(r) for r in self._results.values()),
        }
