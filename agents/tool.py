"""
Tool — 工具基类

AI Member 可调用的操作。每个工具定义权限要求和置信度阈值。
Group 执行 Tool 前检查 Member 权限，置信度不足时请求共识。

子类需实现:
    execute(ctx: SharedContext, **kwargs) → ToolResult

属性:
    name                 工具名（如 "create_student"）
    description          工具描述（LLM 看到的）
    permission           所需权限（如 "student:write"）
    confidence_required  自动执行所需最低置信度
    parameters           JSON Schema 参数定义
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str = ""


class Tool:
    name: str = ""
    description: str = ""
    parameters: dict = None
    permission: str = ""
    confidence_required: float = 0.0

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not cls.name:
            cls.name = cls.__name__
        if cls.parameters is None:
            cls.parameters = {}

    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {},
            },
        }

    async def execute(self, ctx: "SharedContext", **kwargs) -> ToolResult:
        raise NotImplementedError
