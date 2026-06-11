"""
研究工具：search_papers / analyze_code / run_code

导师和研究生都可以使用。搜索论文、执行代码、分析代码。
"""
import json
from pathlib import Path

from agents.tool import Tool, ToolResult
from agents.context import SharedContext


class SearchPapers(Tool):
    name = "search_papers"
    description = "搜索学术论文（Semantic Scholar API）"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "最大结果数，默认5"},
        },
        "required": ["query"],
    }
    permission = "research:read"
    confidence_required = 0.0

    async def execute(self, ctx, **kwargs):
        query = kwargs["query"]
        max_results = kwargs.get("max_results", 5)
        try:
            from lib.semantic_scholar import search_for_papers
            papers = search_for_papers(query, result_limit=max_results)
            if not papers:
                return ToolResult(success=True, data={"papers": [], "count": 0})
            summaries = []
            for p in papers:
                authors = ", ".join(a.get("name", "?") for a in p.get("authors", []))
                summaries.append({
                    "title": p.get("title", ""),
                    "authors": authors,
                    "year": p.get("year"),
                    "venue": p.get("venue", ""),
                    "citations": p.get("citationCount", 0),
                    "abstract": (p.get("abstract") or "")[:500],
                })
            return ToolResult(success=True, data={"papers": summaries, "count": len(summaries)})
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class RunCode(Tool):
    name = "run_code"
    description = "在沙盒中执行 Python 代码并返回结果"
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "要执行的 Python 代码"},
            "timeout": {"type": "integer", "description": "超时秒数，默认300"},
        },
        "required": ["code"],
    }
    permission = "research:write"
    confidence_required = 0.5

    async def execute(self, ctx: SharedContext, *, code: str,
                      timeout: int = 300) -> ToolResult:
        try:
            from lib.interpreter import Interpreter
            interp = Interpreter(working_dir=ctx.root, timeout=timeout)
            result = interp.run(code, reset_session=True)
            return ToolResult(success=result.exc_type is None, data={
                "output": "\n".join(result.term_out),
                "exec_time": result.exec_time,
                "exc_type": result.exc_type,
                "exc_info": result.exc_info,
            })
        except Exception as e:
            return ToolResult(success=False, error=str(e))
