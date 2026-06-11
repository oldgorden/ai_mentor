"""
agents/tools/improve.py — 论文改进工具

CritiquePaper: 读取旧实验目录，提取论文 + 代码 + 结果，返回结构化上下文。
调用者（通常是 chief 或 reasoning_mentor）基于此上下文分析学术弱点并制定改进计划。
"""
import os
from pathlib import Path

from agents.tool import Tool, ToolResult


class CritiquePaper(Tool):
    name = "critique_paper"
    description = (
        "读取一个已有实验目录，提取论文 LaTeX、实验代码摘要、结果日志、图表列表，"
        "返回结构化上下文供分析学术弱点。"
        "用法：critique_paper(experiment_dir='experiments/2026-06-08_xxx/')"
    )
    parameters = {
        "type": "object",
        "properties": {
            "experiment_dir": {
                "type": "string",
                "description": "旧实验目录路径（包含 latex/ 和 idea.md 的目录）",
            },
            "max_code_lines": {
                "type": "integer",
                "description": "代码摘要最大行数，默认100",
            },
        },
        "required": ["experiment_dir"],
    }
    permission = "research:read"
    confidence_required = 0.0

    async def execute(self, ctx, **kwargs):
        exp_dir = Path(kwargs["experiment_dir"])
        if not exp_dir.is_absolute():
            exp_dir = ctx.root / exp_dir

        if not exp_dir.exists():
            return ToolResult(success=False, error=f"目录不存在: {exp_dir}")

        max_code = kwargs.get("max_code_lines", 100)
        result = {}

        result["experiment_dir"] = str(exp_dir)
        result["dir_name"] = exp_dir.name

        latex_path = exp_dir / "latex" / "template.tex"
        if latex_path.exists():
            result["paper_latex"] = latex_path.read_text()
        else:
            result["paper_latex"] = None

        idea_path = exp_dir / "idea.md"
        if idea_path.exists():
            lines = idea_path.read_text().splitlines()
            result["idea_header"] = "\n".join(lines[:max_code])
        else:
            idea_md = exp_dir / "idea.json"
            if idea_md.exists():
                import json
                with open(idea_md) as f:
                    data = json.load(f)
                result["idea_header"] = data
            else:
                result["idea_header"] = None

        figures_dir = exp_dir / "figures"
        if figures_dir.exists():
            result["figures"] = sorted(os.listdir(figures_dir))
        else:
            for d in sorted(exp_dir.iterdir()):
                if d.is_dir() and d.name.endswith("_imgs"):
                    result["figures_dir"] = sorted(os.listdir(d))
                    break

        runs = []
        for d in sorted(exp_dir.iterdir()):
            if d.is_dir() and d.name.endswith("-run"):
                has_code = (d / "input" / "runfile.py").exists() if (d / "input").exists() else False
                has_working = (d / "working").exists()
                runs.append({
                    "name": d.name,
                    "has_code": has_code,
                    "has_working": has_working,
                })
        result["runs"] = runs
        result["num_runs"] = len(runs)

        logs_dir = exp_dir / "logs"
        if logs_dir.exists():
            log_runs = sorted([d.name for d in logs_dir.iterdir() if d.is_dir()])
            result["logged_runs"] = log_runs
        else:
            result["logged_runs"] = []

        log_path = exp_dir / "experiment.log"
        if log_path.exists():
            lines = log_path.read_text().splitlines()
            result["experiment_log_tail"] = "\n".join(lines[-50:])
        else:
            result["experiment_log_tail"] = None

        bfts_config = exp_dir / "bfts_config.yaml"
        result["from_bfts"] = bfts_config.exists()

        cached_bib = exp_dir / "cached_citations.bib"
        if cached_bib.exists():
            result["cached_citations"] = cached_bib.read_text()[:3000]
        else:
            result["cached_citations"] = None

        return ToolResult(success=True, data=result)
