"""
agents/ — AI-First Agent 框架

AI 作为决策者，通过事件驱动 + 工具调用来管理科研实验。
导师和研究生都是 Member，统一在 group_member.json 中配置。

核心流程:
    事件 → Group 路由 → Member LLM 决策 → Tool 执行 → 结果存入 Context

成员类型:
    member_type="mentor"     导师（出脑力：指导、审稿、决策）
    member_type="postgrad"   研究生（干脏活：实验、写论文、搜文献）

文件说明:
    event.py     异步事件总线（Event + EventBus）
    tool.py      Tool 基类（name/description/permission/confidence/execute）
    context.py   共享上下文（操作历史/想法列表）
    member.py    Member 类（角色/权限/置信度/LLM 决策，导师和研究生通用）
    group.py     Group 类（团队地基，路由事件/共识/执行工具）
    run_agent.py 入口，读取 group_member.json 启动事件循环

子目录:
    tools/           工具实现（experiment/research/literature/writing/review/journal/knowledge/project）
    prompts/         导师角色 prompt 模板（chief/code/reasoning/multimodal.md）

配置:
    group_member.json        团队组成（导师 + 研究生 / 事件路由 / 约束）
    mentor/config.local.json API 凭证（密钥）

启动:
    python agents/run_agent.py
    python agents/run_agent.py --group my_team.json
"""
from agents.event import Event, EventBus
from agents.tool import Tool, ToolResult
from agents.context import SharedContext
from agents.member import Member, Action
from agents.group import Group
