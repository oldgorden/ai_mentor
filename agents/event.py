"""
Event + EventBus — 异步事件系统

Event 是数据载体（type + data + timestamp）。
EventBus 是发布/订阅中心，用 asyncio.Queue 解耦生产者和消费者。

事件类型:
    student_exit     学生进程退出（exit_code, has_good_nodes）
    new_node         journal 新增节点（node_count, new_nodes）
    student_stuck    无进展超时（stuck_duration）
    context_overflow 上下文超限（token_count）
    manual           用户手动触发（action, message）
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine


@dataclass
class Event:
    type: str
    data: dict = field(default_factory=dict)
    timestamp: float = 0.0
    source: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    _STOP_SENTINEL = object()

    def __init__(self):
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers: dict[str, list[EventHandler]] = {}

    async def publish(self, event: Event):
        await self._queue.put(event)
        handlers = self._subscribers.get(event.type, []) + self._subscribers.get("*", [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                logging.exception("[EventBus] handler error for event=%s", event.type)

    def subscribe(self, event_type: str, handler: EventHandler):
        self._subscribers.setdefault(event_type, []).append(handler)

    async def next_event(self, timeout: float = None) -> Event:
        if timeout:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        return await self._queue.get()

    def stop(self):
        self._queue.put_nowait(self._STOP_SENTINEL)

    async def __aiter__(self):
        while True:
            item = await self._queue.get()
            if item is self._STOP_SENTINEL:
                break
            yield item
