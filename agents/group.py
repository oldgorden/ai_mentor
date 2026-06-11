"""
Group — 团队地基

管理成员(Member)、工具(Tool)、任务队列(TaskQueue)、事件路由和实验锁。

流程:
    1. 收到事件 → 路由到对应 Member
    2. chief_mentor 分析事件，通过 AssignTask 分配任务给其他成员
    3. 被分配的成员从 TaskQueue 取任务，执行，结果回传
    4. chief 审查结果，决定下一步

竞态保护:
    - RunExperiment 执行时获取实验目录锁
    - WriteExperimentCode / EditFile 检查锁，有锁拒绝写入
"""
import asyncio
import json
import os
import time
from typing import Optional


from pathlib import Path

from agents.event import Event, EventBus
from agents.member import Member, Action
from agents.tool import Tool, ToolResult
from agents.context import SharedContext
from agents.task_queue import TaskQueue, Task, TaskResult


class Group:
    def __init__(self, name: str, root: Path = None, config: dict = None):
        self.name = name
        self.root = root or Path.cwd()
        self.config = config or {}

        self.members: dict[str, Member] = {}
        self.tools: dict[str, Tool] = {}
        self.context = SharedContext(self.root)
        self.event_bus = EventBus()
        self.task_queue = TaskQueue()
        self.context._task_queue = self.task_queue
        self.constraints = {
            "max_retries": 3,
            "check_interval": self.config.get("check_interval", 30),
        }

        self._event_routing: dict[str, str] = {
            "start_experiment": "chief_mentor",
            "improve_paper": "chief_mentor",
            "experiment_retry": "chief_mentor",
            "experiment_success": "chief_mentor",
            "experiment_failed": "chief_mentor",
            "code_review": "code_mentor",
            "logic_check": "reasoning_mentor",
            "literature_search": "multimodal_mentor",
            "context_overflow": "chief_mentor",
            "manual": "chief_mentor",
        }

        self._running = False
        self._log_file = self.root / "mentor" / "logs" / "agent_actions.jsonl"
        self._log_file.parent.mkdir(parents=True, exist_ok=True)

    def add_member(self, member: Member):
        self.members[member.name] = member

    def remove_member(self, name: str):
        self.members.pop(name, None)

    def register_tool(self, tool: Tool):
        self.tools[tool.name] = tool

    def set_event_routing(self, event_type: str, member_name: str):
        self._event_routing[event_type] = member_name

    def route_event(self, event: Event) -> Optional[Member]:
        member_name = self._event_routing.get(event.type, "chief_mentor")
        member = self.members.get(member_name)
        if member is None:
            print(f"[warn] route_event: '{member_name}' not found for event '{event.type}', falling back to chief_mentor")
            member = self.members.get("chief_mentor")
        return member

    async def execute_action(self, member: Member, action: Action,
                             event: Event = None) -> ToolResult:
        tool = self.tools.get(action.tool_name)
        if tool is None:
            return ToolResult(success=False, error=f"Tool not found: {action.tool_name}")
        if not member.has_permission(tool.permission):
            return ToolResult(success=False, error=f"Permission denied: {tool.permission}")

        evt_type = event.type if event else ""
        evt_data = event.data if event else {}

        try:
            self.context.active_member = member
            result = await tool.execute(self.context, **action.params)
            self.context.record_decision(
                member=member.name, event_type=evt_type, event_data=evt_data,
                thought=action.thought, tool_name=action.tool_name,
                tool_params=action.params, confidence=action.confidence,
                result_success=result.success, result_data=result.data,
            )
            member.inject_tool_result(action.tool_name, action.params, result)
            self._log_action(member, action, result)
            return result
        except Exception as e:
            result = ToolResult(success=False, error=str(e))
            self.context.record_decision(
                member=member.name, event_type=evt_type, event_data=evt_data,
                thought=action.thought, tool_name=action.tool_name,
                tool_params=action.params, confidence=action.confidence,
                result_success=False, result_error=str(e),
            )
            member.inject_tool_result(action.tool_name, action.params, result)
            self._log_action(member, action, result)
            return result

    async def consensus(self, action: Action, proposer: Member) -> bool:
        if proposer.confidence >= 0.8:
            return True
        chief = self.members.get("chief_mentor")
        if chief is None or chief is proposer:
            return True

        approve_event = Event(
            type="consensus_check",
            data={
                "proposer": proposer.name,
                "tool": action.tool_name,
                "params": action.params,
                "thought": action.thought,
                "confidence": action.confidence,
            },
        )
        chief_action = await chief.decide(self.context, approve_event, self.tools)
        if chief_action is None:
            return True
        return chief_action.tool_name != "reject"

    async def run(self):
        self._running = True
        self.context.config = self.config

        self.context.load_state()

        ideas_file = self.config.get("ideas_file", "")
        if ideas_file:
            self.context.load_ideas(ideas_file)

        for member in self.members.values():
            try:
                member.init_client()
                member.set_context(self.context)
            except Exception as e:
                print(f"[group] {member.name} init failed: {e}")

        print(f"[group] {self.name} started, {len(self.members)} members, {len(self.tools)} tools")

        save_interval = self.config.get("save_interval", 300)
        last_save = time.time()

        async for event in self.event_bus:
            if not self._running:
                break

            print(f"[group] event: {event.type} {json.dumps(event.data, ensure_ascii=False)[:200]}")

            if event.type == "start_experiment":
                exp_dir = event.data.get("experiment_dir") or self._make_exp_dir(event.data)
                member = self.route_event(event)
                if member:
                    result = await self.run_experiment_loop(member, exp_dir)
                    if result and result.success:
                        await self.event_bus.publish(Event("experiment_success", {
                            "experiment_dir": exp_dir,
                            "metrics": result.data,
                        }))
                    else:
                        await self.event_bus.publish(Event("experiment_failed", {
                            "experiment_dir": exp_dir,
                        }))
                continue

            if event.type == "improve_paper":
                old_dir = event.data.get("old_experiment_dir", "")
                member = self.route_event(event)
                if member:
                    try:
                        result = await self.run_improve_loop(member, old_dir)
                    except Exception as e:
                        print(f"[error] improve_loop crashed: {e}")
                        import traceback
                        traceback.print_exc()
                continue

            member = self.route_event(event)
            if member is None:
                continue

            action = await member.decide(self.context, event, self.tools)
            if action is None:
                print(f"[group] {member.name}: no action needed")
                continue

            print(f"[group] {member.name} -> {action.tool_name}({json.dumps(action.params, ensure_ascii=False)[:100]}) "
                  f"thought={action.thought[:80]} confidence={action.confidence:.2f}")

            tool = self.tools.get(action.tool_name)
            if tool is None:
                print(f"[group] tool not found: {action.tool_name}")
                continue

            if action.confidence < tool.confidence_required:
                approved = await self.consensus(action, member)
                if not approved:
                    print(f"[group] action rejected (confidence={action.confidence:.2f} < {tool.confidence_required})")
                    continue

            result = await self.execute_action(member, action, event)
            status = "OK" if result.success else f"FAIL: {result.error}"
            print(f"[group] {action.tool_name} -> {status}")

            if time.time() - last_save > save_interval:
                self.context.save_state()
                last_save = time.time()

        self.context.save_state()

    def _make_exp_dir(self, event_data: dict) -> str:
        import datetime
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        idea = event_data.get("idea", "experiment")
        slug = idea.lower().replace(" ", "_")[:30]
        exp_dir = str(self.root / "experiments" / f"{ts}_{slug}_attempt0")
        os.makedirs(exp_dir, exist_ok=True)
        return exp_dir

    async def stop(self):
        self._running = False
        self.event_bus.stop()

    async def _visual_review(self, old_experiment_dir: str) -> str:
        mm_mentor = self.members.get("multimodal_mentor")
        if mm_mentor is None:
            return ""

        review_event = Event("visual_figure_review", {
            "experiment_dir": old_experiment_dir,
            "instruction": (
                f"旧论文目录: {old_experiment_dir}\n"
                f"请用 critique_paper 工具读取旧论文，然后分析图表：\n"
                f"1. 论文有哪些图表？（从 LaTeX 和 figures 列表判断）\n"
                f"2. 每个图表展示了什么实验？标题/图注说明了什么？\n"
                f"3. 有无学术问题？（无误差棒、Y轴截断、样本量小、指标异常高）\n"
                f"4. 改进时需要重做哪些图表？\n\n"
                f"简洁回答，每个图表 1-2 句。"
            ),
        })

        try:
            action = await mm_mentor.decide(self.context, review_event, self.tools)
            if action and action.thought:
                return f"## multimodal_mentor 图表分析\n{action.thought}"
        except Exception as e:
            print(f"[improve] visual review failed: {e}")

        return ""

    async def _review_research_sufficiency(self, chief: Member,
                                            research_steps: int,
                                            new_dir: str,
                                            old_experiment_dir: str = "") -> dict:
        reviewer = self.members.get("reasoning_mentor")
        if reviewer is None:
            return {"sufficient": True, "reason": "no reviewer available"}

        research_summary = self._summarize_chief_research(chief)

        visual_analysis = ""
        if old_experiment_dir:
            visual_analysis = await self._visual_review(old_experiment_dir)

        visual_section = ""
        if visual_analysis:
            visual_section = f"\n\n{visual_analysis}"

        review_event = Event("research_sufficiency_review", {
            "experiment_dir": new_dir,
            "research_steps": research_steps,
            "chief_history_summary": research_summary,
            "instruction": (
                f"chief_mentor 已做 {research_steps} 步研究。\n\n"
                f"## chief 的研究记录\n{research_summary}\n"
                f"{visual_section}\n\n"
                f"请评估研究是否充分：\n"
                f"1. 弱点是否已识别？\n"
                f"2. 基线方法/数据集是否已了解？\n"
                f"3. 图表是否暴露了问题？（无误差棒、样本量小、指标异常）\n"
                f"4. 改进方案是否有足够信息支撑？\n\n"
                f"返回 JSON：\n"
                f'{{"sufficient": true/false, "reason": "理由", "missing": ["缺什么"]}}'
            ),
        })

        try:
            action = await reviewer.decide(self.context, review_event, self.tools)
            if action and action.params:
                return {
                    "sufficient": action.params.get("sufficient", True),
                    "reason": action.params.get("reason", ""),
                    "missing": action.params.get("missing", []),
                }
        except Exception as e:
            print(f"[improve] sufficiency review failed: {e}")

        return {"sufficient": True, "reason": "review parse failed"}

    def _summarize_chief_research(self, chief: Member) -> str:
        tools_used = []
        files_read = []
        searches = []
        findings = []

        for msg in chief._msg_history:
            content = msg.get("content", "")
            role = msg.get("role", "")

            if role == "assistant":
                try:
                    import re
                    thought_m = re.search(r'"thought"\s*:\s*"([^"]*)"', content)
                    if thought_m:
                        findings.append(f"[分析] {thought_m.group(1)[:200]}")
                except Exception:
                    pass
                continue

            if "工具执行结果" not in content:
                continue

            tool_name_m = None
            try:
                import json as _json
                if '"tool"' in content:
                    start = content.index('"tool"')
                    snippet = content[start:start + 80]
                    tool_name_m = snippet.split('"')[3] if len(snippet.split('"')) > 3 else None
            except Exception:
                pass

            params_m = None
            try:
                if '"params"' in content:
                    ps = content.index('"params"')
                    pe = content.index('}', ps)
                    params_m = content[ps:pe + 1][:200]
            except Exception:
                pass

            success = '"success": true' in content or '"success":True' in content

            if tool_name_m in ("read_file", "search_code", "list_files"):
                path = ""
                if params_m and "path" in params_m:
                    import re
                    pm = re.search(r'"path"\s*:\s*"([^"]*)"', params_m)
                    if pm:
                        path = pm.group(1)
                if path:
                    files_read.append(path)
                data_preview = ""
                if '"data"' in content:
                    ds = content.index('"data"')
                    data_preview = content[ds:ds + 300]
                tools_used.append(f"read → {path or '?'} | {'OK' if success else 'FAIL'} | {data_preview[:100]}")

            elif tool_name_m in ("search_papers", "search_literature"):
                query = ""
                if params_m and "query" in params_m:
                    import re
                    qm = re.search(r'"query"\s*:\s*"([^"]*)"', params_m)
                    if qm:
                        query = qm.group(1)
                if query:
                    searches.append(query)
                data_preview = ""
                if '"data"' in content:
                    ds = content.index('"data"')
                    data_preview = content[ds:ds + 300]
                tools_used.append(f"search → {query or '?'} | {data_preview[:100]}")

            elif tool_name_m == "critique_paper":
                data_preview = ""
                if '"data"' in content:
                    ds = content.index('"data"')
                    data_preview = content[ds:ds + 500]
                tools_used.append(f"critique | {data_preview[:300]}")

            else:
                tools_used.append(f"{tool_name_m or '?'} | {'OK' if success else 'FAIL'}")

        parts = []
        if tools_used:
            parts.append("## 已执行的工具调用\n" + "\n".join(f"- {t}" for t in tools_used))
        if files_read:
            parts.append(f"## 已读文件\n" + ", ".join(set(files_read)))
        if searches:
            parts.append(f"## 已搜关键词\n" + ", ".join(set(searches)))
        if findings:
            parts.append("## chief 的分析\n" + "\n".join(f"- {f}" for f in findings))

        return "\n\n".join(parts) if parts else "无研究记录"

    async def run_improve_loop(self, member: Member, old_experiment_dir: str) -> Optional[ToolResult]:
        import datetime
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        old_name = Path(old_experiment_dir).name
        new_dir = str(self.root / "experiments" / f"{ts}_improve_{old_name}")
        os.makedirs(new_dir, exist_ok=True)

        chief = self.members.get("chief_mentor")
        if chief is None:
            print("[improve] no chief_mentor found")
            return None

        chief._msg_history = []
        steps = 0
        max_steps = 80

        RESEARCH_TOOLS = {"read_file", "search_code", "list_files", "web_fetch",
                          "search_papers", "search_literature", "critique_paper",
                          "read_journal", "read_kb"}
        CODE_TOOLS = {"WriteExperimentCode", "edit_file"}
        research_steps = 0
        soft_limit = 8
        hard_limit = 20

        event = Event("improve_paper", {
            "old_experiment_dir": old_experiment_dir,
            "new_experiment_dir": new_dir,
            "instruction": (
                f"旧论文在: {old_experiment_dir}\n"
                f"新实验目录: {new_dir}\n\n"
                f"第一步：用 critique_paper 读取旧论文，分析学术弱点。\n"
                f"第二步：制定改进计划（修复数据、增加基线、统计检验、消融实验）。\n"
                f"第三步：分配任务给 code_mentor 写改进实验代码。\n"
                f"第四步：分配任务给 researcher_postgrad 跑实验。\n"
                f"第五步：审查结果，不合格则迭代修改。\n"
                f"第六步：分配 writer_postgrad 写改进论文。\n"
                f"第七步：分配 reviewer_postgrad 审稿。\n"
                f"审稿通过后不返回 action（tool=null）。"
            ),
        })

        api_retries = 0
        max_api_retries = 3
        while steps < max_steps and self._running:
            steps += 1
            action = await chief.decide(self.context, event, self.tools)
            if action is None:
                api_retries += 1
                if api_retries <= max_api_retries:
                    import asyncio as _aio
                    wait = min(30 * api_retries, 120)
                    print(f"[improve] step {steps}: chief returned None (attempt {api_retries}/{max_api_retries}), waiting {wait}s...")
                    await _aio.sleep(wait)
                    continue
                print(f"[improve] step {steps}: chief done after {api_retries} retries")
                return None
            api_retries = 0

            if action.tool_name in RESEARCH_TOOLS:
                research_steps += 1
                if research_steps >= hard_limit:
                    print(f"[improve] step {steps}: research hard limit ({research_steps}), forcing action")
                    chief._msg_history.append({
                        "role": "user",
                        "content": (
                            f"[系统] 你已做了 {research_steps} 步研究，远超预算 {hard_limit}。"
                            f"你不能再做任何研究。你只有两个选择：\n"
                            f"1. WriteExperimentCode 写实验代码\n"
                            f"2. assign_task 把任务分配给别人\n"
                            f"必须立刻选一个。"
                        ),
                    })
                    continue
                if research_steps >= soft_limit and research_steps % 4 == 0:
                    remaining = hard_limit - research_steps
                    review = await self._review_research_sufficiency(
                        chief, research_steps, new_dir, old_experiment_dir)
                    if review.get("sufficient"):
                        event = Event("research_sufficient", {
                            "experiment_dir": new_dir,
                            "instruction": (
                                f"推理导师评估：研究已充分。{review.get('reason', '')}\n"
                                f"现在可以写代码或分配任务。如果还需要少量研究也可以继续。"
                            ),
                        })
                    else:
                        missing = review.get("missing", [])
                        missing_str = "、".join(missing) if missing else review.get("reason", "")
                        event = Event("research_checkpoint", {
                            "experiment_dir": new_dir,
                            "instruction": (
                                f"推理导师：还缺 {missing_str}（剩余 {remaining} 步研究预算）。\n"
                                f"针对性补充，不要重复已读内容。"
                            ),
                        })
                    continue

            tool = self.tools.get(action.tool_name)
            if tool is None:
                print(f"[improve] step {steps}: tool not found: {action.tool_name}")
                continue

            if action.confidence < tool.confidence_required:
                approved = await self.consensus(action, chief)
                if not approved:
                    continue

            result = await self.execute_action(chief, action, event)
            status = "OK" if result.success else f"FAIL: {result.error}"
            print(f"[improve] step {steps}: {action.tool_name} -> {status}")

            if action.tool_name == "assign_task" and result.success:
                assignee_name = action.params.get("assign_to", "")
                assignee = self.members.get(assignee_name)
                if assignee:
                    task_result = await self._run_member_task(assignee, new_dir, max_steps=15)
                    if task_result:
                        event = Event("task_completed", {
                            "assignee": assignee_name,
                            "experiment_dir": new_dir,
                            "result": task_result,
                        })
                    else:
                        event = Event("task_failed", {
                            "assignee": assignee_name,
                            "experiment_dir": new_dir,
                        })
                else:
                    event = Event("step_ok", {"experiment_dir": new_dir})

            elif action.tool_name == "RunExperiment" and result.success:
                stdout = result.data.get("stdout", "") if result.data else ""
                event = Event("experiment_results_ready", {
                    "experiment_dir": new_dir,
                    "stdout": stdout[-3000:],
                    "exec_time": result.data.get("exec_time", 0) if result.data else 0,
                })
            elif action.tool_name == "critique_paper" and result.success:
                event = Event("critique_done", {
                    "old_experiment_dir": old_experiment_dir,
                    "new_experiment_dir": new_dir,
                    "critique": result.data,
                    "instruction": (
                        f"已读取旧论文。请分析学术弱点并制定改进计划。\n"
                        f"新实验目录: {new_dir}\n"
                        f"改进完成后用 assign_task 分配任务给团队成员执行。"
                    ),
                })
            elif result.success:
                event = Event("step_ok", {
                    "experiment_dir": new_dir,
                    "last_tool": action.tool_name,
                })
            else:
                error_msg = result.error or ""
                if result.data and isinstance(result.data, dict):
                    stderr = result.data.get("stderr", "")
                    if stderr:
                        error_msg = stderr[:500]
                event = Event("experiment_retry", {
                    "experiment_dir": new_dir,
                    "last_error": error_msg,
                })

        print(f"[improve] exhausted {max_steps} steps")
        return None

    async def _run_member_task(self, member: Member, experiment_dir: str,
                                max_steps: int = 15) -> dict:
        task_queue = getattr(self.context, '_task_queue', None)
        if task_queue is None:
            return None

        task = await task_queue.next_task(member.name, timeout=5)
        if task is None:
            print(f"[improve] no task found for {member.name}")
            return None

        print(f"[improve] {member.name} picking up task: {task.task_type} - {task.description[:80]}")

        member._msg_history = []
        event = Event("task_assigned", {
            "task_id": task.task_id,
            "task_type": task.task_type,
            "description": task.description,
            "experiment_dir": task.experiment_dir or experiment_dir,
            "params": task.params,
        })

        last_result = None
        api_retries = 0
        for step in range(max_steps):
            action = await member.decide(self.context, event, self.tools)
            if action is None:
                api_retries += 1
                if api_retries <= 3:
                    import asyncio as _aio
                    wait = min(30 * api_retries, 120)
                    print(f"[improve] {member.name} step {step}: returned None (retry {api_retries}/3), waiting {wait}s...")
                    await _aio.sleep(wait)
                    continue
                print(f"[improve] {member.name} step {step}: done after {api_retries} retries")
                break
            api_retries = 0

            tool = self.tools.get(action.tool_name)
            if tool is None:
                continue

            result = await self.execute_action(member, action, event)
            last_result = result
            status = "OK" if result.success else f"FAIL: {result.error}"
            print(f"[improve] {member.name} step {step}: {action.tool_name} -> {status}")

            if result.success:
                if action.tool_name == "RunExperiment":
                    stdout = result.data.get("stdout", "") if result.data else ""
                    event = Event("experiment_results_ready", {
                        "experiment_dir": experiment_dir,
                        "stdout": stdout[-2000:],
                        "exec_time": result.data.get("exec_time", 0) if result.data else 0,
                    })
                else:
                    event = Event("step_ok", {
                        "experiment_dir": experiment_dir,
                        "last_tool": action.tool_name,
                    })
            else:
                error_msg = result.error or ""
                if result.data and isinstance(result.data, dict):
                    stderr = result.data.get("stderr", "")
                    if stderr:
                        error_msg = stderr[:300]
                event = Event("task_retry", {
                    "experiment_dir": experiment_dir,
                    "last_error": error_msg,
                })

        result_payload = {
            "task_id": task.task_id,
            "task_type": task.task_type,
            "success": last_result.success if last_result else False,
            "data": last_result.data if last_result and last_result.success else None,
            "error": last_result.error if last_result and not last_result.success else None,
        }
        task_queue.submit_result(TaskResult(
            task_id=task.task_id,
            task_type=task.task_type,
            completed_by=member.name,
            success=result_payload["success"],
            data=result_payload["data"],
            error=result_payload.get("error", ""),
        ))
        return result_payload

    async def run_experiment_loop(self, member: Member, experiment_dir: str,
                                  max_retries: int = 3) -> Optional[ToolResult]:
        last_error = ""
        failures = 0
        steps = 0
        max_steps = max_retries * 4 + 10
        member._msg_history = []

        event = Event("start_experiment", {
            "experiment_dir": experiment_dir,
            "attempt": 0,
            "instruction": f"实验目录: {experiment_dir}。请用 WriteExperimentCode 写入该目录，然后用 RunExperiment 执行。",
        })

        while steps < max_steps and self._running:
            steps += 1
            action = await member.decide(self.context, event, self.tools)
            if action is None:
                if event.type == "experiment_results_ready":
                    print(f"[loop] step {steps}: mentor satisfied with results, done")
                    return result
                print(f"[loop] step {steps}: no action, stopping")
                return None

            tool = self.tools.get(action.tool_name)
            if tool is None:
                print(f"[loop] tool not found: {action.tool_name}")
                continue

            if action.confidence < tool.confidence_required:
                approved = await self.consensus(action, member)
                if not approved:
                    continue

            result = await self.execute_action(member, action, event)
            status = "OK" if result.success else f"FAIL: {result.error}"
            print(f"[loop] step {steps}: {action.tool_name} -> {status}")

            if result.success:
                if action.tool_name == "RunExperiment":
                    stdout = result.data.get("stdout", "") if result.data else ""
                    event = Event("experiment_results_ready", {
                        "experiment_dir": experiment_dir,
                        "stdout": stdout[-3000:],
                        "exec_time": result.data.get("exec_time", 0) if result.data else 0,
                        "steps": steps,
                        "failures": failures,
                    })
                elif action.tool_name == "WriteExperimentCode" and "results_ready" in str(event.data):
                    run_event = Event("run_after_fix", {
                        "experiment_dir": experiment_dir,
                        "reason": "代码已修改，请立即执行 RunExperiment",
                    })
                    event = run_event
                else:
                    event = Event("step_ok", {
                        "experiment_dir": experiment_dir,
                        "last_tool": action.tool_name,
                        "steps": steps,
                    })
            else:
                failures += 1
                last_error = result.error or ""
                if result.data and isinstance(result.data, dict):
                    stderr = result.data.get("stderr", "")
                    if stderr:
                        last_error = stderr[:500]
                if failures >= max_retries:
                    print(f"[loop] exhausted {max_retries} failures")
                    return None
                event = Event("experiment_retry", {
                    "experiment_dir": experiment_dir,
                    "attempt": failures,
                    "last_error": last_error,
                })

        print(f"[loop] exhausted {max_steps} steps")
        return None

    def _log_action(self, member: Member, action: Action, result: ToolResult):
        try:
            entry = {
                "timestamp": time.time(),
                "member": member.name,
                "member_type": member.member_type,
                "tool": action.tool_name,
                "params": action.params,
                "thought": action.thought,
                "confidence": action.confidence,
                "success": result.success,
                "data": result.data,
                "error": result.error,
            }
            with open(self._log_file, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[warn] _log_action failed: {e}")
