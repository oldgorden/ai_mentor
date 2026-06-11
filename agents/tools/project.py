"""
agents/tools/project.py — 项目管理工具

AI Member 可通过这些工具管理研究项目（创建、查看、更新）。
"""
from agents.tool import Tool, ToolResult


class CreateProject(Tool):
    name = "CreateProject"
    description = (
        "创建一个新的研究项目。项目是研究方向的容器，包含多个实验尝试。"
        "需要提供项目名称、研究目标、目标会议等。"
    )
    permission = "project:write"
    confidence_required = 0.7
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "项目唯一标识名（英文，下划线分隔）"},
            "title": {"type": "string", "description": "项目标题（可含中文）"},
            "goal": {"type": "string", "description": "研究目标和核心问题"},
            "idea_file": {"type": "string", "description": "研究想法 JSON 文件路径"},
            "target_venue": {"type": "string", "description": "目标会议/期刊"},
            "page_limit": {"type": "integer", "description": "论文页数限制", "default": 4},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "标签"},
        },
        "required": ["name", "goal"],
    }

    async def execute(self, ctx, **kwargs) -> ToolResult:
        from lib.project import ProjectManager
        pm = ProjectManager(ctx.root)
        try:
            proj = pm.create(
                name=kwargs["name"],
                title=kwargs.get("title", kwargs["name"]),
                goal=kwargs["goal"],
                idea_file=kwargs.get("idea_file", ""),
                target_venue=kwargs.get("target_venue", ""),
                page_limit=kwargs.get("page_limit", 4),
                tags=kwargs.get("tags", []),
            )
            return ToolResult(success=True, data=proj.to_dict())
        except FileExistsError as e:
            return ToolResult(success=False, error=str(e))


class ListProjects(Tool):
    name = "ListProjects"
    description = (
        "列出所有研究项目及其状态。可按状态过滤。"
        "返回项目名称、状态、实验次数、最新实验状态。"
    )
    permission = "project:read"
    confidence_required = 0.0
    parameters = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "按状态过滤: draft/active/completed/archived"},
        },
    }

    async def execute(self, ctx, **kwargs) -> ToolResult:
        from lib.project import ProjectManager
        pm = ProjectManager(ctx.root)
        projects = pm.list_projects(status=kwargs.get("status"))
        summary = []
        for p in projects:
            latest = p.get_latest_attempt()
            summary.append({
                "name": p.name,
                "title": p.title,
                "status": p.status,
                "target_venue": p.target_venue,
                "n_attempts": len(p.attempts),
                "latest_attempt": latest.path if latest else None,
                "latest_status": latest.status if latest else None,
                "goal": p.goal[:100] if p.goal else "",
            })
        return ToolResult(success=True, data=summary)


class UpdateProject(Tool):
    name = "UpdateProject"
    description = (
        "更新项目信息：状态、目标、备注等。"
        "也可以添加论文审查结果。"
    )
    permission = "project:write"
    confidence_required = 0.5
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "项目名"},
            "status": {"type": "string", "description": "新状态: draft/active/completed/archived"},
            "notes": {"type": "string", "description": "项目备注"},
            "goal": {"type": "string", "description": "更新研究目标"},
            "add_review": {"type": "object", "description": "添加审查结果 {severity, issues, summary}"},
        },
        "required": ["name"],
    }

    async def execute(self, ctx, **kwargs) -> ToolResult:
        from lib.project import ProjectManager
        pm = ProjectManager(ctx.root)
        try:
            proj = pm.load(kwargs["name"])
        except FileNotFoundError as e:
            return ToolResult(success=False, error=str(e))

        if "status" in kwargs:
            try:
                proj.set_status(kwargs["status"])
            except ValueError as e:
                return ToolResult(success=False, error=str(e))

        if "notes" in kwargs:
            proj.notes = kwargs["notes"]
            proj.updated_at = __import__("time").time()

        if "goal" in kwargs:
            proj.goal = kwargs["goal"]
            proj.updated_at = __import__("time").time()

        if "add_review" in kwargs:
            rev = kwargs["add_review"]
            proj.add_review(
                reviewer="ai_mentor",
                severity=rev.get("severity", ""),
                issues=rev.get("issues", []),
                summary=rev.get("summary", ""),
            )

        pm.save(proj)
        return ToolResult(success=True, data={"name": proj.name, "status": proj.status})


class ScanExperiments(Tool):
    name = "ScanExperiments"
    description = (
        "扫描 experiments/ 目录，将匹配的实验自动关联到项目。"
        "根据目录名中的关键词匹配项目名。"
    )
    permission = "project:write"
    confidence_required = 0.3
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "项目名"},
        },
        "required": ["name"],
    }

    async def execute(self, ctx, **kwargs) -> ToolResult:
        from lib.project import ProjectManager
        pm = ProjectManager(ctx.root)
        try:
            proj = pm.scan_experiments(kwargs["name"])
            return ToolResult(success=True, data={
                "name": proj.name,
                "n_attempts": len(proj.attempts),
            })
        except FileNotFoundError as e:
            return ToolResult(success=False, error=str(e))
