"""
文献工具：search_literature / get_paper_details

封装 lib/tools/semantic_scholar.py。
研究生和导师都可以用，搜索和检索论文。
"""
import os
import json
from agents.tool import Tool, ToolResult
from agents.context import SharedContext


class SearchLiterature(Tool):
    name = "search_literature"
    description = "搜索学术论文（Semantic Scholar），返回标题、作者、摘要、引用数。比 search_papers 更适合研究生做文献综述。"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "最大结果数，默认10"},
        },
        "required": ["query"],
    }
    permission = "literature:read"
    confidence_required = 0.0

    async def execute(self, ctx: SharedContext, *, query: str,
                      max_results: int = 10) -> ToolResult:
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


class GetPaperDetails(Tool):
    name = "get_paper_details"
    description = "获取论文的引用格式（citationStyles）"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "精确论文标题或关键词"},
        },
        "required": ["query"],
    }
    permission = "literature:read"
    confidence_required = 0.0

    async def execute(self, ctx: SharedContext, *, query: str) -> ToolResult:
        try:
            from lib.semantic_scholar import search_for_papers
            papers = search_for_papers(query, result_limit=1)
            if not papers:
                return ToolResult(success=False, error="Paper not found")
            paper = papers[0]
            citations = paper.get("citationStyles", {})
            return ToolResult(success=True, data={
                "title": paper.get("title", ""),
                "citations": citations,
                "year": paper.get("year"),
            })
        except Exception as e:
            return ToolResult(success=False, error=str(e))
