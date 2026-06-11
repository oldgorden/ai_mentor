import os
import json
import time
import sys
import asyncio

from agents.tool import Tool, ToolResult
from agents.experiment_lock import acquire_lock, release_lock, is_locked


class WriteExperimentCode(Tool):
    name = "WriteExperimentCode"
    description = (
        "将实验代码写入文件（整文件覆盖）。"
        "用法：生成新的实验代码、重写有 bug 的代码、写多模型对比脚本。"
        "代码必须 import sys; sys.path.insert(0, '<ROOT>')，"
        "通过 lib.experiment_env 加载数据（key='mask' 不是 'segmentation'）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "experiment_dir": {"type": "string", "description": "Experiment directory path"},
            "code": {"type": "string", "description": "Python code to write"},
            "filename": {"type": "string", "description": "Filename (default: runfile.py)"},
            "stage": {"type": "string", "description": "Stage name (e.g. initial, tuning, ablation)"},
        },
        "required": ["experiment_dir", "code"],
    }
    permission = "experiment:write"
    confidence_required = 0.3

    async def execute(self, ctx, **kwargs):
        exp_dir = kwargs["experiment_dir"]
        filename = kwargs.get("filename", "runfile.py")
        code = kwargs["code"]
        stage = kwargs.get("stage", "unknown")

        if is_locked(exp_dir):
            return ToolResult(success=False, error=f"实验目录被锁定（正在执行中），不能写入: {exp_dir}")

        code = code.replace("<ROOT>", str(ctx.root))
        os.makedirs(exp_dir, exist_ok=True)
        filepath = os.path.join(exp_dir, filename)

        with open(filepath, "w") as f:
            f.write(code)

        return ToolResult(
            success=True,
            data={
                "filepath": filepath,
                "size_bytes": len(code),
                "stage": stage,
            },
        )


class RunExperiment(Tool):
    name = "RunExperiment"
    description = (
        "执行实验代码，返回 stdout/stderr/执行时间/返回码。"
        "用法：执行 runfile.py 查看训练结果和指标。"
        "自动设置 PYTHONPATH，代码可 import lib。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "experiment_dir": {"type": "string", "description": "Experiment directory path"},
            "filename": {"type": "string", "description": "Filename to execute (default: runfile.py)"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default: 3600)"},
        },
        "required": ["experiment_dir"],
    }
    permission = "experiment:write"
    confidence_required = 0.5

    async def execute(self, ctx, **kwargs):
        exp_dir = kwargs["experiment_dir"]
        filename = kwargs.get("filename", "runfile.py")
        timeout = kwargs.get("timeout", 3600)

        filepath = os.path.join(exp_dir, filename)
        if not os.path.exists(filepath):
            return ToolResult(success=False, error=f"File not found: {filepath}")

        if is_locked(exp_dir):
            return ToolResult(success=False, error=f"实验目录已被锁定（另一个实验正在执行）")

        if not acquire_lock(exp_dir):
            return ToolResult(success=False, error="无法获取实验锁")

        t0 = time.time()
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        root = str(ctx.root) if hasattr(ctx, 'root') else ""
        if root:
            env["PYTHONPATH"] = root + ":" + env.get("PYTHONPATH", "")
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-u", filepath,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=exp_dir,
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                elapsed = time.time() - t0
                release_lock(exp_dir)
                return ToolResult(
                    success=False,
                    data={
                        "returncode": -1,
                        "stdout": "",
                        "stderr": f"Timeout after {timeout}s",
                        "exec_time": round(elapsed, 1),
                        "timed_out": True,
                    },
                )

            elapsed = time.time() - t0
            stdout_text = stdout.decode(errors="replace")
            stderr_text = stderr.decode(errors="replace")
            stdout_str = stdout_text[-8000:] if len(stdout_text) > 8000 else stdout_text
            stderr_str = stderr_text[-4000:] if len(stderr_text) > 4000 else stderr_text

            release_lock(exp_dir)
            return ToolResult(
                success=proc.returncode == 0,
                data={
                    "returncode": proc.returncode,
                    "stdout": stdout_str,
                    "stderr": stderr_str,
                    "exec_time": round(elapsed, 1),
                    "timed_out": False,
                },
            )
        except Exception as e:
            release_lock(exp_dir)
            return ToolResult(success=False, error=str(e))


class ValidateResults(Tool):
    name = "ValidateResults"
    description = (
        "验证实验代码质量：检查是否用了真实数据(experiment_env)、是否引用旧数据集、是否有输出目录。"
        "用法：执行前审查代码，防止数据泄露或过拟合。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "experiment_dir": {"type": "string", "description": "Experiment directory path"},
            "checks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Checks to perform: data_real, metrics_sane, files_exist, no_synthetic",
            },
        },
        "required": ["experiment_dir"],
    }
    permission = "experiment:read"
    confidence_required = 0.0

    async def execute(self, ctx, **kwargs):
        exp_dir = kwargs["experiment_dir"]
        checks = kwargs.get("checks", ["data_real", "metrics_sane", "files_exist"])
        issues = []
        warnings = []

        runfile = os.path.join(exp_dir, "runfile.py")
        if not os.path.exists(runfile):
            return ToolResult(success=False, error=f"No runfile.py in {exp_dir}")

        code = ""
        with open(runfile) as f:
            code = f.read()

        if "data_real" in checks or "no_synthetic" in checks:
            if "create_synthetic_dataset" in code and "experiment_env" not in code:
                issues.append("CRITICAL: Code uses synthetic data generation, not real datasets")
            if "experiment_env" in code:
                warnings.append("GOOD: Code imports from experiment_env (controlled data layer)")
            if "scene_parse150" in code or "voc2012" in code or "coco_stuff" in code:
                if "cityscapes" not in code:
                    issues.append("WARNING: Code references old datasets but not Cityscapes")

        if "metrics_sane" in checks:
            for npy_file in ["experiment_data.npy", "results.npy"]:
                npy_path = os.path.join(exp_dir, "working", npy_file)
                if os.path.exists(npy_path):
                    warnings.append(f"Found data file: {npy_path}")

        if "files_exist" in checks:
            working_dir = os.path.join(exp_dir, "working")
            if os.path.exists(working_dir):
                pngs = [f for f in os.listdir(working_dir) if f.endswith(".png")]
                pths = [f for f in os.listdir(working_dir) if f.endswith(".pth")]
                npys = [f for f in os.listdir(working_dir) if f.endswith(".npy")]
                warnings.append(f"Output files: {len(pngs)} plots, {len(pths)} models, {len(npys)} data")
            else:
                issues.append("No working/ directory found - experiment may not have run")

        return ToolResult(
            success=len(issues) == 0,
            data={"issues": issues, "warnings": warnings, "code_length": len(code)},
        )


class ReadExperimentOutput(Tool):
    name = "ReadExperimentOutput"
    description = (
        "读取 experiment.log 的尾部内容。"
        "用法：实验跑完后查看日志、检查训练 loss 曲线、确认最终指标。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "experiment_dir": {"type": "string", "description": "Experiment directory path"},
            "max_lines": {"type": "integer", "description": "Max lines to return (default: 100)"},
        },
        "required": ["experiment_dir"],
    }
    permission = "experiment:read"
    confidence_required = 0.0

    async def execute(self, ctx, **kwargs):
        exp_dir = kwargs["experiment_dir"]
        max_lines = kwargs.get("max_lines", 100)

        log_path = os.path.join(exp_dir, "experiment.log")
        if not os.path.exists(log_path):
            return ToolResult(
                success=False,
                error=f"No experiment.log in {exp_dir}",
            )

        with open(log_path) as f:
            lines = f.readlines()[-max_lines:]

        return ToolResult(
            success=True,
            data={"log_tail": "".join(lines), "total_lines": len(lines)},
        )
