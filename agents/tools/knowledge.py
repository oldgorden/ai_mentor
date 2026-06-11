"""
知识工具：read_kb / write_kb / compress

导师和研究生共享的知识库系统。
permanent（永久）、temporary（临时）、postgrad（研究生专属）三级。
"""
import json
from pathlib import Path

from agents.tool import Tool, ToolResult
from agents.context import SharedContext


class ReadKB(Tool):
    name = "read_kb"
    description = "读取知识库内容（permanent/temporary/postgrad）"
    parameters = {
        "type": "object",
        "properties": {
            "kb_type": {"type": "string", "enum": ["permanent", "temporary", "postgrad"]},
            "postgrad_name": {"type": "string", "description": "kb_type=postgrad 时指定研究生名"},
            "category": {"type": "string", "description": "按类别过滤（可选）"},
            "limit": {"type": "integer", "description": "返回条数上限，默认20"},
        },
        "required": ["kb_type"],
    }
    permission = "knowledge:read"
    confidence_required = 0.0

    async def execute(self, ctx: SharedContext, *, kb_type: str,
                      postgrad_name: str = None, category: str = None,
                      limit: int = 20) -> ToolResult:
        try:
            if kb_type == "postgrad":
                if not postgrad_name:
                    return ToolResult(success=False, error="postgrad_name required for postgrad KB")
                path = ctx.root / "mentor" / f"postgrad_{postgrad_name}_kb.json"
            elif kb_type == "permanent":
                path = ctx.root / "mentor" / "permanent_kb.json"
            elif kb_type == "temporary":
                path = ctx.root / "mentor" / "temporary_kb.json"
            else:
                return ToolResult(success=False, error=f"Unknown kb_type: {kb_type}")

            if not path.exists():
                return ToolResult(success=True, data={"entries": []})

            with open(path) as f:
                data = json.load(f)

            entries = data if isinstance(data, list) else data.get("entries", [])
            if category:
                entries = [e for e in entries if e.get("category") == category]

            return ToolResult(success=True, data={
                "entries": entries[:limit],
                "total": len(entries),
            })
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class WriteKB(Tool):
    name = "write_kb"
    description = "向知识库写入一条记录"
    parameters = {
        "type": "object",
        "properties": {
            "kb_type": {"type": "string", "enum": ["temporary", "postgrad"]},
            "postgrad_name": {"type": "string"},
            "category": {"type": "string", "description": "failure/success/consensus/decision"},
            "content": {"type": "string"},
            "importance": {"type": "integer", "description": "1-5"},
        },
        "required": ["kb_type", "category", "content"],
    }
    permission = "knowledge:write"
    confidence_required = 0.3

    MAX_KB_ENTRIES = 200

    async def execute(self, ctx: SharedContext, *, kb_type: str,
                      category: str, content: str, postgrad_name: str = None,
                      importance: int = 3) -> ToolResult:
        try:
            if kb_type == "postgrad":
                if not postgrad_name:
                    return ToolResult(success=False, error="postgrad_name required")
                path = ctx.root / "mentor" / f"postgrad_{postgrad_name}_kb.json"
            else:
                path = ctx.root / "mentor" / "temporary_kb.json"

            entries = []
            if path.exists():
                with open(path) as f:
                    data = json.load(f)
                entries = data if isinstance(data, list) else data.get("entries", [])

            entry = {
                "category": category,
                "content": content,
                "importance": importance,
                "timestamp": __import__("time").time(),
            }
            entries.append(entry)

            if len(entries) > self.MAX_KB_ENTRIES:
                entries.sort(key=lambda e: (e.get("importance", 3), e.get("timestamp", 0)), reverse=True)
                entries = entries[:self.MAX_KB_ENTRIES]

            with open(path, "w") as f:
                json.dump({"entries": entries}, f, ensure_ascii=False, indent=2)

            return ToolResult(success=True, data={"written": True, "total_entries": len(entries)})
        except Exception as e:
            return ToolResult(success=False, error=str(e))
