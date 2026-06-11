"""
Journal 工具：read_journal / summarize_logs

读取实验目录的 journal.json，查看节点和执行记录。
"""
import json
import os
from pathlib import Path

from agents.tool import Tool, ToolResult
from agents.context import SharedContext


class ReadJournal(Tool):
    name = "read_journal"
    description = "读取实验目录的 journal.json，返回节点列表（计划、代码摘要、执行结果、评分）"
    parameters = {
        "type": "object",
        "properties": {
            "experiment_dir": {"type": "string", "description": "实验目录路径"},
            "include_buggy": {"type": "boolean", "description": "是否包含失败节点，默认 false"},
            "limit": {"type": "integer", "description": "返回节点数上限，默认10"},
        },
        "required": ["experiment_dir"],
    }
    permission = "journal:read"
    confidence_required = 0.0

    async def execute(self, ctx: SharedContext, *, experiment_dir: str,
                      include_buggy: bool = False, limit: int = 10) -> ToolResult:
        candidates = [
            os.path.join(experiment_dir, "journal.json"),
            os.path.join(experiment_dir, "working", "journal.json"),
        ]
        journal_path = None
        for c in candidates:
            if os.path.exists(c):
                journal_path = c
                break

        if not journal_path:
            return ToolResult(success=False, error=f"No journal.json in {experiment_dir}")

        try:
            with open(journal_path) as f:
                data = json.load(f)

            nodes = data if isinstance(data, list) else data.get("nodes", [])

            if not include_buggy:
                nodes = [n for n in nodes if not n.get("is_buggy")]

            summaries = []
            for n in nodes[:limit]:
                summaries.append({
                    "id": n.get("id", "")[:8],
                    "step": n.get("step"),
                    "plan": (n.get("plan") or "")[:200],
                    "is_buggy": n.get("is_buggy", False),
                    "metric": n.get("metric", {}),
                    "exec_time": n.get("exec_time"),
                })

            total = len(nodes)
            buggy = sum(1 for n in (data if isinstance(data, list) else data.get("nodes", [])) if n.get("is_buggy"))

            return ToolResult(success=True, data={
                "total_nodes": total + buggy,
                "good_nodes": total,
                "buggy_nodes": buggy,
                "nodes": summaries,
            })
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class SummarizeLogs(Tool):
    name = "summarize_logs"
    description = "压缩实验日志为摘要"
    parameters = {
        "type": "object",
        "properties": {
            "experiment_dir": {"type": "string", "description": "实验目录路径"},
        },
        "required": ["experiment_dir"],
    }
    permission = "journal:write"
    confidence_required = 0.3

    async def execute(self, ctx: SharedContext, *, experiment_dir: str) -> ToolResult:
        candidates = [
            os.path.join(experiment_dir, "journal.json"),
            os.path.join(experiment_dir, "working", "journal.json"),
        ]
        journal_path = None
        for c in candidates:
            if os.path.exists(c):
                journal_path = c
                break

        if not journal_path:
            return ToolResult(success=False, error=f"No journal.json in {experiment_dir}")

        try:
            with open(journal_path) as f:
                journal_data = json.load(f)
            nodes_data = journal_data if isinstance(journal_data, list) else journal_data.get("nodes", [])

            summaries = []
            for n in nodes_data:
                if not n.get("is_buggy"):
                    summaries.append({
                        "plan": (n.get("plan") or "")[:300],
                        "step": n.get("step"),
                        "metric": n.get("metric", {}),
                    })

            return ToolResult(success=True, data={
                "total_nodes": len(nodes_data),
                "summaries": summaries[:20],
            })
        except Exception as e:
            return ToolResult(success=False, error=str(e))
