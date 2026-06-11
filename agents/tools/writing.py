"""
写作工具：write_paper / generate_plots

封装 lib/perform_writeup.py 和 perform_plotting.py。
研究生用来撰写论文和生成图表。
"""
import os
import sys
import asyncio
import json
from pathlib import Path

from agents.tool import Tool, ToolResult
from agents.context import SharedContext


class WritePaper(Tool):
    name = "write_paper"
    description = "为实验生成完整的 LaTeX 论文（调用 lib/perform_writeup.py）"
    parameters = {
        "type": "object",
        "properties": {
            "exp_dir": {"type": "string", "description": "实验目录路径"},
            "model": {"type": "string", "description": "写论文用的模型，留空用默认"},
        },
        "required": ["exp_dir"],
    }
    permission = "writing:write"
    confidence_required = 0.7

    async def execute(self, ctx: SharedContext, *, exp_dir: str,
                      model: str = "") -> ToolResult:
        exp_path = Path(exp_dir)
        if not exp_path.is_absolute():
            exp_path = ctx.root / exp_dir
        if not exp_path.exists():
            return ToolResult(success=False, error=f"Experiment dir not found: {exp_dir}")

        if not model:
            model = "custom/mimo-v2.5-pro"

        env = os.environ.copy()
        for k in ["ALL_PROXY", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
            env.pop(k, None)
        env["AI_SCIENTIST_ROOT"] = str(ctx.root)

        writeup_script = ctx.root / "lib" / "writeup.py"
        cmd = [
            sys.executable,
            str(writeup_script),
            "--exp_dir", str(exp_path),
            "--model", model,
            "--model_citation", model,
            "--model_review", model,
            "--model_agg_plots", model,
            "--model_writeup_small", model,
        ]

        log_path = ctx.root / "mentor" / "logs" / f"writeup_{exp_path.name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with open(log_path, "w") as log_f:
            proc = await asyncio.create_subprocess_exec(
                *cmd, env=env, cwd=str(ctx.root),
                stdout=log_f, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )

        return ToolResult(success=True, data={
            "exp_dir": str(exp_path),
            "pid": proc.pid,
            "log": str(log_path),
        })


class GeneratePlots(Tool):
    name = "generate_plots"
    description = "为实验生成聚合图表（调用 lib/perform_plotting.py）"
    parameters = {
        "type": "object",
        "properties": {
            "exp_dir": {"type": "string", "description": "实验目录路径"},
            "model": {"type": "string", "description": "生成图表用的模型，留空用默认"},
        },
        "required": ["exp_dir"],
    }
    permission = "writing:write"
    confidence_required = 0.5

    async def execute(self, ctx: SharedContext, *, exp_dir: str,
                      model: str = "") -> ToolResult:
        exp_path = Path(exp_dir)
        if not exp_path.is_absolute():
            exp_path = ctx.root / exp_dir
        if not exp_path.exists():
            return ToolResult(success=False, error=f"Experiment dir not found: {exp_dir}")

        if not model:
            model = "custom/mimo-v2.5-pro"

        env = os.environ.copy()
        for k in ["ALL_PROXY", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
            env.pop(k, None)
        env["AI_SCIENTIST_ROOT"] = str(ctx.root)

        plotting_script = ctx.root / "lib" / "plotting.py"
        cmd = [
            sys.executable,
            str(plotting_script),
            "--exp_dir", str(exp_path),
            "--model", model,
        ]

        log_path = ctx.root / "mentor" / "logs" / f"plot_{exp_path.name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with open(log_path, "w") as log_f:
            proc = await asyncio.create_subprocess_exec(
                *cmd, env=env, cwd=str(ctx.root),
                stdout=log_f, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )

        return ToolResult(success=True, data={
            "exp_dir": str(exp_path),
            "pid": proc.pid,
            "log": str(log_path),
        })
