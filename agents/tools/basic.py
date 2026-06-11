"""
基本能力工具：读文件、搜代码、浏览目录、编辑文件、执行命令、抓网页

导师和研究生都需要这些基本操作来：
- 读取项目中的任意文件（代码、配置、日志）
- 搜索代码库定位函数/类/错误
- 浏览目录结构
- 局部编辑已有文件
- 执行 shell 命令
- 抓取网页内容（文档、论文、API 说明）
"""
import os
import subprocess
import sys
import asyncio

from agents.tool import Tool, ToolResult
from agents.experiment_lock import is_locked


class ReadFile(Tool):
    name = "read_file"
    description = (
        "读取任意文件内容（代码、配置、日志等）。"
        "用法：当代码报错时读源码定位 bug、读日志查错误、读配置确认参数。"
        "支持 offset/limit 分页读取大文件。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件绝对路径"},
            "offset": {"type": "integer", "description": "起始行号（1-based），默认1"},
            "limit": {"type": "integer", "description": "读取行数上限，默认200"},
        },
        "required": ["path"],
    }
    permission = "research:read"
    confidence_required = 0.0

    async def execute(self, ctx, **kwargs):
        path = kwargs["path"]
        offset = max(1, kwargs.get("offset", 1))
        limit = kwargs.get("limit", 200)

        if not os.path.exists(path):
            return ToolResult(success=False, error=f"File not found: {path}")
        if os.path.getsize(path) > 5 * 1024 * 1024:
            return ToolResult(success=False, error=f"File too large: {os.path.getsize(path)} bytes")

        try:
            with open(path) as f:
                lines = f.readlines()
            selected = lines[offset - 1 : offset - 1 + limit]
            total = len(lines)
            text = "".join(selected)
            return ToolResult(success=True, data={
                "content": text,
                "total_lines": total,
                "showing": f"{offset}-{min(offset + limit - 1, total)}",
                "path": path,
            })
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class SearchCode(Tool):
    name = "search_code"
    description = (
        "在代码库中搜索文本（类似 grep）。"
        "用法：找函数定义(search_code 'def train')、找报错位置(search_code 'KeyError')、"
        "找 import 来源(search_code 'from lib.')、找类定义(search_code 'class UNet')。"
        "返回 文件名:行号:匹配内容。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "搜索模式（支持正则）"},
            "path": {"type": "string", "description": "搜索目录，默认项目根目录"},
            "include": {"type": "string", "description": "文件名过滤，如 '*.py'"},
            "max_results": {"type": "integer", "description": "最大结果数，默认30"},
        },
        "required": ["pattern"],
    }
    permission = "research:read"
    confidence_required = 0.0

    async def execute(self, ctx, **kwargs):
        import re

        pattern = kwargs["pattern"]
        search_dir = kwargs.get("path", str(ctx.root))
        include = kwargs.get("include", "")
        max_results = kwargs.get("max_results", 30)

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult(success=False, error=f"Invalid regex: {e}")

        results = []
        for root, dirs, files in os.walk(search_dir):
            dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", "node_modules", ".idea"}]
            for fname in files:
                if include and not _glob_match(fname, include):
                    continue
                fpath = os.path.join(root, fname)
                if not _is_text_file(fpath):
                    continue
                try:
                    with open(fpath) as f:
                        for i, line in enumerate(f, 1):
                            if regex.search(line):
                                rel = os.path.relpath(fpath, search_dir)
                                results.append(f"{rel}:{i}: {line.rstrip()}")
                                if len(results) >= max_results:
                                    return ToolResult(success=True, data={
                                        "matches": results,
                                        "total": len(results),
                                        "truncated": True,
                                    })
                except Exception:
                    continue

        return ToolResult(success=True, data={
            "matches": results,
            "total": len(results),
            "truncated": False,
        })


class ListFiles(Tool):
    name = "list_files"
    description = (
        "列出目录内容或按文件名搜索（类似 ls / find）。"
        "用法：浏览项目结构(list_files)、找某类文件(list_files pattern='*.py' recursive=true)、"
        "查看实验目录内容(list_files path=实验目录)。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径，默认项目根目录"},
            "pattern": {"type": "string", "description": "文件名过滤模式，如 '*.py'"},
            "recursive": {"type": "boolean", "description": "是否递归搜索子目录，默认 false"},
        },
        "required": [],
    }
    permission = "research:read"
    confidence_required = 0.0

    async def execute(self, ctx, **kwargs):
        search_dir = kwargs.get("path", str(ctx.root))
        pattern = kwargs.get("pattern", "")
        recursive = kwargs.get("recursive", False)

        if not os.path.isdir(search_dir):
            return ToolResult(success=False, error=f"Not a directory: {search_dir}")

        entries = []
        if recursive:
            for root, dirs, files in os.walk(search_dir):
                dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", "node_modules"}]
                for f in files:
                    if pattern and not _glob_match(f, pattern):
                        continue
                    rel = os.path.relpath(os.path.join(root, f), search_dir)
                    entries.append(rel)
        else:
            for entry in sorted(os.listdir(search_dir)):
                if pattern and not _glob_match(entry, pattern):
                    continue
                full = os.path.join(search_dir, entry)
                if os.path.isdir(full):
                    entries.append(entry + "/")
                else:
                    entries.append(entry)

        return ToolResult(success=True, data={
            "path": search_dir,
            "entries": entries[:200],
            "total": len(entries),
        })


class EditFile(Tool):
    name = "edit_file"
    description = (
        "编辑文件：用新文本替换指定旧文本（类似 sed）。"
        "用法：修复 bug(edit_file old='batch[\"seg\"]' new='batch[\"mask\"]')、"
        "调参数(edit_file old='epochs=3' new='epochs=10')、修改代码片段。"
        "注意：old_text 必须在文件中唯一出现，否则报错。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件绝对路径"},
            "old_text": {"type": "string", "description": "要替换的原文本"},
            "new_text": {"type": "string", "description": "替换后的新文本"},
        },
        "required": ["path", "old_text", "new_text"],
    }
    permission = "research:write"
    confidence_required = 0.3

    async def execute(self, ctx, **kwargs):
        path = kwargs["path"]
        old_text = kwargs["old_text"]
        new_text = kwargs["new_text"]

        if not os.path.exists(path):
            return ToolResult(success=False, error=f"File not found: {path}")

        exp_dir = os.path.dirname(path)
        if exp_dir and is_locked(exp_dir):
            return ToolResult(success=False, error=f"文件所在目录被锁定（实验执行中），不能编辑")

        try:
            with open(path) as f:
                content = f.read()

            count = content.count(old_text)
            if count == 0:
                return ToolResult(success=False, error="old_text not found in file")
            if count > 1:
                return ToolResult(success=False, error=f"old_text found {count} times, not unique. Provide more context.")

            new_content = content.replace(old_text, new_text, 1)
            with open(path, "w") as f:
                f.write(new_content)

            return ToolResult(success=True, data={
                "path": path,
                "replacements": 1,
                "old_length": len(old_text),
                "new_length": len(new_text),
            })
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class RunShell(Tool):
    name = "run_shell"
    description = (
        "执行 shell 命令。"
        "用法：查 GPU(run_shell 'nvidia-smi')、看磁盘(run_shell 'df -h')、"
        "安装包(run_shell 'pip install xxx')、查看进程(run_shell 'ps aux | grep python')。"
        "注意：不要用这个跑实验代码，用 RunExperiment。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell 命令"},
            "timeout": {"type": "integer", "description": "超时秒数，默认30"},
        },
        "required": ["command"],
    }
    permission = "research:write"
    confidence_required = 0.5

    async def execute(self, ctx, **kwargs):
        command = kwargs["command"]
        timeout = kwargs.get("timeout", 30)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(ctx.root),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            stdout_text = stdout.decode(errors="replace")
            stderr_text = stderr.decode(errors="replace")
            stdout_str = stdout_text[-4000:] if len(stdout_text) > 4000 else stdout_text
            stderr_str = stderr_text[-2000:] if len(stderr_text) > 2000 else stderr_text

            return ToolResult(
                success=proc.returncode == 0,
                data={
                    "returncode": proc.returncode,
                    "stdout": stdout_str,
                    "stderr": stderr_str,
                },
            )
        except asyncio.TimeoutError:
            proc.kill()
            return ToolResult(success=False, error=f"Timeout after {timeout}s")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


def _glob_match(name, pattern):
    import fnmatch
    return fnmatch.fnmatch(name, pattern)


def _is_text_file(path):
    try:
        if os.path.getsize(path) > 500000:
            return False
        with open(path, errors="ignore") as f:
            f.read(1024)
        return True
    except Exception:
        return False


class WebFetch(Tool):
    name = "web_fetch"
    description = (
        "抓取网页内容，转为纯文本返回。"
        "用法：查文档(web_fetch 'https://pytorch.org/docs/...')、"
        "读论文摘要(web_fetch arxiv 链接)、查看 GitHub README、"
        "读 API 文档确认用法。自动去除 HTML 标签。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL"},
            "max_length": {"type": "integer", "description": "返回文本最大字符数，默认5000"},
        },
        "required": ["url"],
    }
    permission = "research:read"
    confidence_required = 0.0

    async def execute(self, ctx, **kwargs):
        url = kwargs["url"]
        max_length = kwargs.get("max_length", 5000)

        if not url.startswith(("http://", "https://")):
            return ToolResult(success=False, error="URL must start with http:// or https://")

        try:
            import aiohttp
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Accept": "text/html,application/json,text/plain",
            }
            async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return ToolResult(success=False, error=f"HTTP {resp.status}")
                    content_type = resp.headers.get("Content-Type", "")
                    body = await resp.text()

            if "application/json" in content_type:
                text = body[:max_length]
            else:
                text = _html_to_text(body)[:max_length]

            return ToolResult(success=True, data={
                "url": url,
                "content_type": content_type,
                "text": text,
                "length": len(text),
            })
        except Exception as e:
            return ToolResult(success=False, error=str(e))


def _html_to_text(html):
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    except Exception:
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        return text.strip()


class AssignTask(Tool):
    name = "assign_task"
    description = (
        "给团队成员分配任务。chief 专用。"
        "用法：assign_task(assign_to='code_mentor', task_type='code_task', "
        "description='写 LightweightUNet 训练代码', params={...})"
    )
    parameters = {
        "type": "object",
        "properties": {
            "assign_to": {
                "type": "string",
                "description": "分配给谁: code_mentor / researcher_postgrad / reasoning_mentor / multimodal_mentor / writer_postgrad / reviewer_postgrad",
            },
            "task_type": {
                "type": "string",
                "description": "任务类型: code_task / run_task / review_task / literature_task / writeup_task / review_paper",
            },
            "description_field": {
                "type": "string",
                "description": "任务描述（告诉对方做什么、怎么做）。注意：参数名必须用 description_field",
            },
            "params": {
                "type": "object",
                "description": "任务参数（如 experiment_dir, code, filename 等）",
            },
            "experiment_dir": {
                "type": "string",
                "description": "关联的实验目录",
            },
        },
        "required": ["assign_to", "task_type", "description_field"],
    }
    permission = "task:assign"
    confidence_required = 0.0

    async def execute(self, ctx, **kwargs):
        from agents.task_queue import Task

        assign_to = kwargs["assign_to"]
        task_type = kwargs["task_type"]
        desc = kwargs["description_field"]
        params = kwargs.get("params", {})
        exp_dir = kwargs.get("experiment_dir", "")

        task = Task(
            task_type=task_type,
            description=desc,
            created_by=ctx.active_member.name if ctx.active_member else "system",
            params=params,
            experiment_dir=exp_dir,
        )

        task_queue = getattr(ctx, '_task_queue', None)
        if task_queue is None:
            return ToolResult(success=False, error="TaskQueue not available in context")

        await task_queue.submit(task, assign_to)

        return ToolResult(success=True, data={
            "task_id": task.task_id,
            "assigned_to": assign_to,
            "task_type": task_type,
        })


class CheckTaskResults(Tool):
    name = "check_task_results"
    description = (
        "查看已完成的任务结果。chief 用这个审查其他成员的工作成果。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "member_name": {"type": "string", "description": "查看谁的結果，留空看所有人"},
            "n": {"type": "integer", "description": "返回最近几条，默认5"},
        },
        "required": [],
    }
    permission = "task:read"
    confidence_required = 0.0

    async def execute(self, ctx, **kwargs):
        member_name = kwargs.get("member_name")
        n = kwargs.get("n", 5)

        task_queue = getattr(ctx, '_task_queue', None)
        if task_queue is None:
            return ToolResult(success=False, error="TaskQueue not available")

        results = task_queue.get_results(member_name, n)
        summaries = []
        for r in results:
            summaries.append({
                "task_id": r.task_id,
                "type": r.task_type,
                "by": r.completed_by,
                "success": r.success,
                "thought": r.thought[:200],
                "error": r.error[:200] if r.error else None,
            })

        return ToolResult(success=True, data={
            "results": summaries,
            "count": len(summaries),
            "pending": task_queue.pending_count,
        })
