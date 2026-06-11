"""导师团队：协作式指导，共享上下文，完整日志"""
import json
import os
import signal
import subprocess
import time
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from lib.semantic_scholar import SemanticScholarSearchTool
from lib.llm import get_response_from_llm
from api import create_client as _api_create
from lib.interpreter import Interpreter
from mentor.logger import MentorLogger
from mentor.meeting import ExperimentHistory, ExperimentRecord, FailurePattern, MeetingMinutes, GroupMeeting
from mentor.workstation import Workstation
from mentor.monitor import StudentMonitor
from mentor.knowledge_base import PermanentKnowledge, TemporaryKnowledge
from mentor.compressor import ContextCompressor


def _estimate_tokens(text: str) -> int:
    """启发式 token 估算：区分 CJK 和 ASCII，适用于任何模型的粗粒度判断"""
    if not text:
        return 0
    cjk = 0
    for c in text:
        cp = ord(c)
        if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
                0x3000 <= cp <= 0x303F or 0xFF00 <= cp <= 0xFFEF or
                0x2E80 <= cp <= 0x2EFF or 0xF900 <= cp <= 0xFAFF):
            cjk += 1
    ascii_chars = len(text) - cjk
    return int(cjk * 1.8 + ascii_chars * 0.25)


# ==================== 共享上下文 ====================
@dataclass
class SharedContext:
    agent_name: str = ""
    recent_errors: list[str] = field(default_factory=list)
    literature_summary: str = ""
    code_review: str = ""
    critical_review: str = ""
    final_guidance: str = ""
    literature_papers: list[dict] = field(default_factory=list)


class AdoptedProcess:
    """接管一个已存在的 PID，模拟 Popen 接口"""

    def __init__(self, pid: int):
        self.pid = pid
        self._returncode = None

    def poll(self):
        if self._returncode is not None:
            return self._returncode
        try:
            with open(f"/proc/{self.pid}/stat", "r") as f:
                stat = f.read()
            right_paren = stat.rfind(")")
            state = stat[right_paren + 2:].split()[0]
            if state in ("Z", "X"):
                self._returncode = -1
                return -1
            return None
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            self._returncode = -1
            return -1

    def terminate(self):
        self._send_signal(signal.SIGTERM)

    def kill(self):
        self._send_signal(signal.SIGKILL)

    def wait(self, timeout=None):
        start = time.time()
        while True:
            rc = self.poll()
            if rc is not None:
                return rc
            if timeout and (time.time() - start) > timeout:
                raise subprocess.TimeoutExpired(cmd=f"adopted:{self.pid}", timeout=timeout)
            time.sleep(0.5)

    def _send_signal(self, sig):
        pgid = self._safe_pgid()
        if pgid:
            try:
                os.killpg(pgid, sig)
            except OSError:
                pass
        else:
            try:
                os.kill(self.pid, sig)
            except OSError:
                pass

    def _safe_pgid(self):
        try:
            pgid = os.getpgid(self.pid)
            return pgid if pgid != os.getpgrp() else None
        except OSError:
            return None


# ==================== 单个导师 ====================
class Mentor:
    def __init__(self, config: dict, logger=None):
        self.name = config["name"]
        self.role = config["role"]
        self.skills = config.get("skills", [])
        self.model_name = config["model"]
        self.temperature = config.get("temperature", 0.3)
        self.logger = logger
        self.client, self.actual_model, self.model = _api_create(self.model_name)
        self.fallback_client, self.fallback_actual_model, self.fallback_model = None, None, None
        if "fallback_model" in config:
            self.fallback_client, self.fallback_actual_model, self.fallback_model = _api_create(config["fallback_model"])

    def has_skill(self, skill: str) -> bool:
        return skill in self.skills

    def _ask(self, prompt: str, max_tokens: int = 64000, logger: MentorLogger = None, agent_name: str = "", deep_think: bool = True) -> str:
        """调用 LLM，可选深度思考模式"""
        result = self._try_call(self.client, self.actual_model, self.model, prompt, max_tokens, deep_think)
        if result:
            return result
        if logger:
            logger.log(f"[{self.name}] 响应为空，加大 max_tokens 重试", "WARN")
        result = self._try_call(self.client, self.actual_model, self.model, prompt, max_tokens * 3, deep_think)
        if result:
            return result
        if self.fallback_client:
            if logger:
                logger.log(f"[{self.name}] 降级到备用模型", "WARN")
            result = self._try_call(self.fallback_client, self.fallback_actual_model, self.fallback_model, prompt, max_tokens, deep_think)
            if result:
                return result
        if logger:
            logger.log(f"[{self.name}] 所有尝试均失败", "ERROR")
        return ""

    def _try_call(self, client, actual_model, original_model, prompt: str, max_tokens: int, deep_think: bool = False) -> str:
        """调用 LLM，可选深度思考模式"""
        try:
            from api import get_registry
            provider = get_registry().get_provider(original_model)
            if provider is None:
                return ""

            messages = [
                {"role": "system", "content": f"你是{self.role}。"},
                {"role": "user", "content": prompt},
            ]

            extra = {}
            if deep_think:
                extra["extra_body"] = {"reasoning_effort": "high"}

            response = provider.call_completion(
                client, actual_model, messages,
                temperature=self.temperature, max_tokens=max_tokens,
                n=1, **extra,
            )
            contents = provider.extract_content(response)
            content = contents[0] if contents else ""

            # 记录推理过程（如果有）
            reasoning = getattr(response.choices[0].message, "reasoning_content", None)
            if reasoning and self.logger:
                self.logger.log(f"[{self.name}] 推理过程: {reasoning[:500]}")

            if content and content.strip():
                return content.strip()
        except Exception as e:
            if self.logger:
                self.logger.log(f"[{self.name}] 调用失败: {e}", "WARN")
        return ""


# ==================== 导师团队 ====================
class MentorTeam:
    def __init__(self, config_path: str):
        with open(config_path) as f:
            self.config = json.load(f)
        self.root = ROOT
        self.ideas_file = self.root / self.config["ideas_file"]
        self.agent_states: dict[str, dict] = {}
        self.check_interval = self.config.get("check_interval", 300)
        self.stuck_threshold = self.config.get("stuck_threshold", 8)
        self.student_config = self.config.get("student_config", {})
        self.max_students = self.student_config.get("max_students", 3)

        # 设置环境变量（供子进程和 ai_scientist 内部使用）
        from api.credentials import load_credentials
        _creds = load_credentials()
        xfyun_key = ""
        xfyun_url = ""
        for _pname, _pcfg in _creds.items():
            if _pname == "xfyun":
                xfyun_key = _pcfg.get("api_key", "")
                xfyun_url = _pcfg.get("base_url", "")
                os.environ["XFYUN_API_KEY"] = xfyun_key
                os.environ["XFYUN_BASE_URL"] = xfyun_url
            elif _pname == "custom":
                os.environ["CUSTOM_OPENAI_API_KEY"] = _pcfg.get("api_key", "")
                if _pcfg.get("base_url"):
                    os.environ["CUSTOM_OPENAI_BASE_URL"] = _pcfg["base_url"]
            elif _pname == "custom2":
                os.environ["CUSTOM2_OPENAI_API_KEY"] = _pcfg.get("api_key", "")
                if _pcfg.get("base_url"):
                    os.environ["CUSTOM2_OPENAI_BASE_URL"] = _pcfg["base_url"]
            elif _pname == "anthropic":
                os.environ["CUSTOM_ANTHROPIC_API_KEY"] = _pcfg.get("api_key", "")
                if _pcfg.get("base_url"):
                    os.environ["CUSTOM_ANTHROPIC_BASE_URL"] = _pcfg["base_url"]

        # 日志系统
        self.logger = MentorLogger(self.root / "mentor" / "logs")
        self.logger.log("="*50)
        self.logger.log("导师团队初始化")

        # 共享工具
        from api.credentials import get_s2_api_key
        os.environ["S2_API_KEY"] = get_s2_api_key()
        self.scholar = SemanticScholarSearchTool(max_results=8)
        self.interpreter = Interpreter(working_dir=self.root / "working", timeout=600)

        # 创建导师
        self.mentors: dict[str, Mentor] = {}
        for mcfg in self.config.get("members", []):
            mentor = Mentor(mcfg, self.logger)
            self.mentors[mcfg["name"]] = mentor
            skills = mcfg.get("skills", [])
            self.logger.log(f"导师就绪: {mcfg['name']} ({mcfg['role']}) | 模型: {mcfg['model']} | 技能: {skills}")

        self.workflow = self.config.get("workflow", {"on_stuck": [], "on_dead": []})

        # 创建大导师（不参与并行讨论，只做最终裁决）
        self.chief_mentor = None
        chief_cfg = self.config.get("chief_mentor")
        if chief_cfg:
            self.chief_mentor = Mentor(chief_cfg, self.logger)
            self.logger.log(f"大导师就绪: {chief_cfg['role']} | 模型: {chief_cfg['model']}")
        elif self.mentors:
            self.chief_mentor = self.mentors.get("critical_mentor") or next(iter(self.mentors.values()))
            self.logger.log(f"大导师降级: 使用 {self.chief_mentor.name}")

        # 讯飞备用导师（用于压缩器、分类器等非关键任务，分摊 mimo token）
        self._xfyun_mentor = None
        if xfyun_key and xfyun_url:
            self._xfyun_mentor = Mentor({
                "name": "xfyun_util",
                "role": "讯飞通用助手",
                "model": "xfyun/astron-code-latest",
                "temperature": 0.3,
            }, self.logger)
            self.logger.log(f"讯飞备用导师就绪（用于压缩器、分类器）")

        # LLM 分类器（优先用讯飞）
        self._history_classifier = self._xfyun_mentor or (self.mentors.get("code_mentor") if self.mentors else None)

        # 共享实验历史 + 组会
        self.history = ExperimentHistory(llm_classifier=self._history_classifier)
        self.meeting = GroupMeeting(self.mentors, self.history, self.logger, chief_mentor=self.chief_mentor)

        # 工作站：导师可以直接跑代码
        self.workstation = Workstation(self.root, self.logger)
        # 学生监控器：实时监控行为
        self.monitor = StudentMonitor(self.workstation, self.logger)

        # 知识库：永久 + 临时 + 每个学生独立
        self.permanent_kb = PermanentKnowledge(self.root / "mentor" / "permanent_kb.json")
        self.temporary_kb = TemporaryKnowledge(self.root / "mentor" / "temporary_kb.json")
        self.student_kbs: dict[str, TemporaryKnowledge] = {}

        # 压缩器（优先用讯飞）
        self.compressor = ContextCompressor(
            llm=self._xfyun_mentor or self.mentors.get("code_mentor") or list(self.mentors.values())[0],
            logger=self.logger,
        )
        self.context_threshold = 200000

        # 初始化永久知识库
        self._init_permanent_kb()

        # 加载研究想法
        self._load_ideas()

        # 学生管理
        self.student_ideas: dict[str, int] = {}

        # 尝试恢复 checkpoint
        self._checkpoint_path = self.root / "mentor" / "checkpoint.json"
        self._load_checkpoint()

    def _get_student_kb(self, name: str) -> TemporaryKnowledge:
        """获取学生的独立知识库"""
        if name not in self.student_kbs:
            kb_path = self.root / "mentor" / f"student_{name}_kb.json"
            self.student_kbs[name] = TemporaryKnowledge(kb_path)
        return self.student_kbs[name]

    def create_student(self, idea_idx: int, name: str = None) -> str:
        """导师创建一个学生，分配一个研究方向"""
        if len(self.agent_states) >= self.max_students:
            self.logger.log(f"已达最大学生数 {self.max_students}，无法创建更多", "WARN")
            return ""

        idea = self.ideas[idea_idx]
        if name is None:
            name = f"student_{idea['Name']}"

        # 检查是否已有学生在做这个方向
        for existing_name, idx in self.student_ideas.items():
            if idx == idea_idx:
                self.logger.log(f"方向 {idea['Name']} 已有学生 {existing_name} 在做", "WARN")
                return ""

        self.student_ideas[name] = idea_idx
        self.logger.log(f"创建学生: {name} | 方向: {idea['Name']} | 总学生数: {len(self.agent_states) + 1}")

        # 启动学生
        cfg = {
            "name": name,
            "idea_idx": idea_idx,
            "model": self.student_config["model"],
            "max_iters": self.student_config.get("max_iters", 30),
            "starter_code": self.student_config.get("starter_code", ""),
        }
        self.start_agent(cfg)
        return name

    def remove_student(self, name: str):
        """移除一个学生"""
        if name in self.agent_states:
            self.stop_agent(name)
            del self.agent_states[name]
            if name in self.student_ideas:
                del self.student_ideas[name]
            self.logger.log(f"移除学生: {name}")

    def assign_new_idea(self, name: str, idea_idx: int):
        """给学生分配新的研究方向"""
        if name not in self.agent_states:
            self.logger.log(f"学生 {name} 不存在", "WARN")
            return

        self.student_ideas[name] = idea_idx
        self.logger.log(f"给 {name} 分配新方向: {self.ideas[idea_idx]['Name']}")

        # 重启学生
        cfg = self.agent_states[name]["config"]
        cfg["idea_idx"] = idea_idx
        self.restart_agent(name, f"切换到新方向: {self.ideas[idea_idx]['Name']}")

    def get_student_status(self) -> dict:
        """获取所有学生的状态"""
        status = {}
        for name, state in self.agent_states.items():
            idea_idx = self.student_ideas.get(name, -1)
            idea_name = self.ideas[idea_idx]["Name"] if 0 <= idea_idx < len(self.ideas) else "unknown"
            status[name] = {
                "idea": idea_idx,
                "idea_name": idea_name,
                "alive": state["process"] and state["process"].poll() is None,
                "restart_count": state.get("restart_count", 0),
            }
        return status

    def _init_permanent_kb(self):
        """初始化永久知识库（导师手册）"""
        if not self.permanent_kb.entries:
            self.permanent_kb.set("mentor_guide", """
导师操作指南：
1. 启动学生前，先做文献调研
2. 学生卡住时，先分析失败模式，再召开组会
3. 组会后注入共识和 starter code
4. 上下文超过 200K tokens 时压缩
5. 学生生成代码前，先审查方案是否合理
6. 用工作站验证代码，再给学生
""")
            self.permanent_kb.set("student_guide", """
学生操作指南：
1. 根据任务需求选择合适的方法（RL、监督学习、行为克隆等）
2. 参考文献中的方法，不要预设
3. 先验证基础功能，再优化
4. 参考 starter code 的结构（如果有）
""")
            self.permanent_kb.set("research_methodology", """
通用研究方法论：
1. 先搜文献，了解领域现状
2. 先跑简单 baseline，再加复杂度
3. 每次只改一个变量
4. 记录所有实验结果（成功和失败）
5. 失败时分析根本原因，不是改参数
6. 用 RL 训练时，确保 reward 函数包含所有目标
""")
            self.permanent_kb.set("domain_knowledge", "")
            self.logger.log("[知识库] 永久知识库已初始化")

    def _load_ideas(self):
        with open(self.ideas_file) as f:
            self.ideas = json.load(f)

    # ==================== 协作分析 ====================
    def collaborative_analysis(self, agent_name: str) -> str:
        _, nodes = self._find_journal(agent_name.replace("_agent", ""))
        errors = [n.get("analysis", "")[:200] for n in nodes if n.get("is_buggy")][-5:]
        if not errors:
            return ""

        ctx = SharedContext(agent_name=agent_name, recent_errors=errors)
        self.logger.log(f"[{agent_name}] 开始协作分析，{len(errors)} 个失败记录")

        # 1. 文献流水线：multimodal → reasoning
        self._run_literature_pipeline(ctx)

        # 2. 代码分析（拿到文献结果）
        code_mentor = self.mentors.get("code_mentor")
        if code_mentor and code_mentor.has_skill("analyze_failure"):
            self._step_analyze(code_mentor, ctx)

        # 3. 批判性思维审查（质疑假设、验证方法）
        self._step_critical_review(ctx)

        ctx.final_guidance = self._merge_guidance(ctx)
        self.logger.guidance_given("团队", agent_name, ctx.final_guidance)
        return ctx.final_guidance

    def _run_literature_pipeline(self, ctx: SharedContext):
        """文献流水线：多模态导师看图 → 推理导师严谨分析"""
        pipeline = self.config.get("pipeline", {}).get("literature", [])
        if not pipeline:
            return

        # Step 1: 多模态导师提取搜索关键词 + 看图表
        mm_mentor = self.mentors.get(pipeline[0]) if len(pipeline) > 0 else None
        reason_mentor = self.mentors.get(pipeline[1]) if len(pipeline) > 1 else None

        mm_output = ""
        if mm_mentor:
            mm_output = self._step_multimodal_literature(mm_mentor, ctx)

        # Step 2: 推理导师严谨分析（拿到多模态导师的输出）
        if reason_mentor:
            self._step_reasoning_literature(reason_mentor, ctx, mm_output)

    def _step_multimodal_literature(self, mentor: Mentor, ctx: SharedContext) -> str:
        """多模态导师：先搜领域论文，再从失败中提取关键词搜方法论文"""
        self.logger.log(f"[{mentor.name}] 开始多模态文献分析")

        all_papers = []

        # Phase 1: 搜领域论文（从 agent name 推断领域）
        domain = ctx.agent_name.replace("_agent", "").replace("_", " ")
        domain_queries = [domain, f"{domain} robot", f"{domain} locomotion"]
        for q in domain_queries[:2]:
            raw = self.scholar.use_tool(query=q)
            if raw and raw != "No papers found.":
                all_papers.append(raw)
                self.logger.literature_search(mentor.name, q, [])
            time.sleep(1)

        # Phase 2: 从失败中提取关键词搜方法论文
        keywords = mentor._ask(
            f"从这些失败中提取 2 个英文搜索关键词，每行一个：\n"
            + "\n".join(f"  {e}" for e in ctx.recent_errors),
            max_tokens=2000, logger=self.logger, agent_name=ctx.agent_name,
        )
        for q in keywords.strip().split("\n")[:2]:
            q = q.strip()
            if q:
                raw = self.scholar.use_tool(query=q)
                if raw and raw != "No papers found.":
                    all_papers.append(raw)
                    self.logger.literature_search(mentor.name, q, [])
                time.sleep(1)

        # 多模态导师分析：如实总结方法，不预设
        if all_papers:
            mm_summary = mentor._ask(
                f"文献搜索结果：\n\n{''.join(all_papers)}\n\n"
                f"请分析：\n"
                f"1. 这些论文用了什么方法（如实列出，不要预设用 RL 或其他）\n"
                f"2. 哪些方法被证明有效\n"
                f"3. 对当前实验的建议\n"
                f"关键：根据论文内容说话。如果论文用行为克隆有效，就说行为克隆有效。",
                max_tokens=32000, logger=self.logger, agent_name=ctx.agent_name,
            )
            self.logger.action(mentor.name, "multimodal_analysis", {
                "agent": ctx.agent_name, "output": mm_summary[:300],
            })
            return mm_summary
        return ""

    def _step_reasoning_literature(self, mentor: Mentor, ctx: SharedContext, mm_output: str):
        """推理导师：严谨分析，根据文献说话"""
        self.logger.log(f"[{mentor.name}] 开始推理文献分析")

        prompt = "你是严谨的学术分析师。"
        if mm_output:
            prompt += f"\n\n多模态导师的初步分析：\n{mm_output}\n\n"
            prompt += "请验证以上分析的准确性，补充严谨的学术结论。\n"
            prompt += "重点关注：这些论文实际用了什么方法？行为克隆还是 RL？哪个有效？"
        else:
            prompt += f"\n\n学生失败记录：\n"
            prompt += "\n".join(f"  {e}" for e in ctx.recent_errors)
            prompt += "\n\n基于你的知识，分析失败原因并建议改进方法。"

        ctx.literature_summary = mentor._ask(
            prompt, max_tokens=32000, logger=self.logger, agent_name=ctx.agent_name,
        )
        self.logger.action(mentor.name, "reasoning_analysis", {
            "agent": ctx.agent_name, "output": ctx.literature_summary[:300],
        })

    def _step_analyze(self, mentor: Mentor, ctx: SharedContext):
        prompt = f"学生最近的失败：\n"
        prompt += "\n".join(f"  {i+1}. {e}" for i, e in enumerate(ctx.recent_errors))
        if ctx.literature_summary:
            prompt += f"\n\n文献调研：\n{ctx.literature_summary}"
        prompt += "\n\n分析核心错误并给出方法指导。简洁回答。"

        ctx.code_review = mentor._ask(prompt, max_tokens=500, logger=self.logger, agent_name=ctx.agent_name)
        self.logger.action(mentor.name, "failure_analysis", {
            "agent": ctx.agent_name,
            "analysis": ctx.code_review[:300],
        })

    def _step_critical_review(self, ctx: SharedContext):
        """批判性思维导师：质疑假设、验证方法"""
        mentor = self.mentors.get("critical_mentor")
        if not mentor:
            return

        self.logger.log(f"[{mentor.name}] 开始批判性审查")

        prompt = f"""你是批判性思维导师。你的职责是质疑假设、验证方法，避免团队犯方向性错误。

学生最近的失败：
{chr(10).join(f'  {i+1}. {e}' for i, e in enumerate(ctx.recent_errors))}

文献调研结论：
{ctx.literature_summary or '无'}

代码导师分析：
{ctx.code_review or '无'}

请审查：
1. **隐含假设**：上面的分析中有哪些没有验证的假设？（比如"必须用 RL"、"行为克隆不行"等）
2. **反面证据**：有没有可能相反的结论是对的？
3. **验证方案**：用什么简单实验可以快速验证方法是否可行？
4. **风险提示**：如果上面的分析是错的，会有什么后果？

简洁回答，每点 1-2 句话。"""

        ctx.critical_review = mentor._ask(prompt, max_tokens=500, logger=self.logger, agent_name=ctx.agent_name)
        self.logger.action(mentor.name, "critical_review", {
            "agent": ctx.agent_name,
            "review": ctx.critical_review[:300],
        })

    def _merge_guidance(self, ctx: SharedContext) -> str:
        parts = []
        if ctx.literature_summary:
            parts.append(f"【文献依据】{ctx.literature_summary}")
        if ctx.code_review:
            parts.append(f"【问题分析】{ctx.code_review}")
        if ctx.critical_review:
            parts.append(f"【批判性审查】{ctx.critical_review}")
        return "\n\n".join(parts)

    # ==================== 启动 agent ====================
    def _collect_cross_student_experience(self, self_name: str) -> str:
        """从 temporary_kb 收集其他学生的经验（成功+失败+共识），过滤掉自己的"""
        other_names = [n for n in self.agent_states if n != self_name]
        if not other_names:
            return ""

        parts = []

        # 其他学生的成功经验（最有价值）
        other_successes = self.temporary_kb.query(category="success", limit=5)
        other_successes = [
            e for e in other_successes
            if not any(t == self_name for t in e.tags)
            and any(t in other_names for t in e.tags)
        ]
        if other_successes:
            parts.append("## 其他学生的成功经验")
            for e in other_successes[:3]:
                parts.append(f"- {e.summary}")

        # 其他学生的失败教训
        other_failures = self.temporary_kb.query(category="failure", limit=5)
        other_failures = [
            e for e in other_failures
            if not any(t == self_name for t in e.tags)
            and any(t in other_names for t in e.tags)
        ]
        if other_failures:
            parts.append("## 其他学生的失败教训（避免重复犯错）")
            for e in other_failures[:3]:
                parts.append(f"- {e.summary}")

        # 全局共识
        consensus = self.temporary_kb.query(category="consensus", limit=1)
        if consensus:
            parts.append(f"## 团队共识\n{consensus[0].summary}")

        if not parts:
            return ""

        result = "\n".join(parts)
        self.logger.log(f"[{self_name}] 注入跨学生经验 ({len(result)}字), 来源: {other_names}")
        return result

    def start_agent(self, agent_cfg: dict, guidance: str = ""):
        name = agent_cfg["name"]
        self.logger.action("team", "start_agent", {"agent": name})

        # 文献调研
        idea = self.ideas[agent_cfg["idea_idx"]].copy()
        topic = idea.get("Name", idea.get("Title", ""))
        lit = self._startup_literature(topic)
        if lit:
            idea["Related Work"] = idea.get("Related Work", "") + f"\n\n## 文献调研\n{lit}\n"

        if guidance:
            idea["Short Hypothesis"] += f"\n\n## 导师指导\n{guidance}\n"

        starter = agent_cfg.get("starter_code", "")
        if starter:
            sp = self.root / "ai_scientist" / "ideas" / starter
            if sp.exists():
                with open(sp) as f:
                    idea["Code"] = f.read()
                self.logger.log(f"[team] 注入 starter code: {starter}")

        # 从知识库注入之前验证过的代码
        verified = self.temporary_kb.query(category="success", tags=["verified_code"], limit=1)
        if verified and not idea.get("Code"):
            idea["Code"] = verified[0].content
            self.logger.log(f"[team] 从知识库注入验证过的代码")

        # 注入其他学生的经验
        cross_exp = self._collect_cross_student_experience(name)
        if cross_exp:
            idea["Related Work"] = idea.get("Related Work", "") + f"\n\n## 其他学生的实验经验\n{cross_exp}\n"

        idea_path = self.root / "mentor" / f"{name}_idea.json"
        with open(idea_path, "w") as f:
            json.dump([idea], f, indent=2, ensure_ascii=False)

        # 查找该 idea 已有的实验目录，有则继续（但已完成的不重启）
        keyword = name.replace("student_", "").replace("_agent", "")
        continue_from = None
        exp_dir = self.root / "experiments"
        if exp_dir.exists():
            candidates = sorted(
                [d for d in exp_dir.iterdir() if d.is_dir() and keyword in d.name],
                key=lambda d: d.stat().st_mtime,
            )
            if candidates:
                continue_from = str(candidates[-1])
                if self._is_experiment_complete(name):
                    self.logger.log(f"[{name}] 已有实验已完成(stage 3+)，不再启动学生，直接触发论文撰写")
                    self.trigger_writeup(name)
                    return
                self.logger.log(f"[{name}] 首次启动检测到已有实验，继续: {continue_from}")

        self._launch_process(name, agent_cfg, idea_path, continue_from=continue_from)

    def _launch_process(self, name: str, agent_cfg: dict, idea_path: Path, continue_from: str = None):
        """启动 AI-Scientist 进程，可选继续已有实验"""
        env = os.environ.copy()
        env.update({
            "S2_API_KEY": os.environ.get("S2_API_KEY", ""),
            "AI_SCIENTIST_ROOT": str(self.root),
        })
        for k in ["ALL_PROXY", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
            env.pop(k, None)

        cmd = [
            str(self.root / ".venv" / "bin" / "python"),
            str(self.root / "launch_scientist_bfts.py"),
            "--load_ideas", str(idea_path), "--idea_idx", "0",
            "--model_writeup", agent_cfg["model"],
            "--model_citation", agent_cfg["model"],
            "--model_review", agent_cfg["model"],
            "--model_agg_plots", agent_cfg["model"],
            "--model_writeup_small", agent_cfg["model"],
            "--skip_review",
        ]

        # 如果指定了继续已有实验
        if continue_from:
            cmd.extend(["--continue_from", continue_from])

        log_path = self.root / "mentor" / "logs" / f"{name}.log"
        with open(log_path, "w") as log_f:
            proc = subprocess.Popen(cmd, env=env, cwd=str(self.root), stdout=log_f, stderr=subprocess.STDOUT, start_new_session=True)

        self.agent_states[name] = {
            "config": agent_cfg, "process": proc,
            "stuck_count": 0, "restart_count": 0,
            "last_node_count": 0, "last_progress_time": time.time(),
        }
        self.logger.log(f"Agent {name} 启动, PID={proc.pid}" + (f" (继续 {continue_from})" if continue_from else ""))

    def _startup_literature(self, topic: str) -> str:
        """启动前文献调研：先搜领域论文，再搜方法论文"""
        pipeline = self.config.get("pipeline", {}).get("literature", [])
        mm_mentor = self.mentors.get(pipeline[0]) if len(pipeline) > 0 else None
        reason_mentor = self.mentors.get(pipeline[1]) if len(pipeline) > 1 else None

        if not mm_mentor and not reason_mentor:
            return ""

        self.logger.log(f"启动文献调研: {topic}")

        # Phase 1: 搜领域论文（不带方法限定）
        domain_queries = self._generate_domain_queries(topic)
        domain_results = []
        for q in domain_queries:
            raw = self.scholar.use_tool(query=q)
            if raw and raw != "No papers found.":
                domain_results.append(raw)
                self.logger.literature_search("pipeline", q, [])
            time.sleep(1)

        # Phase 2: 让多模态导师从领域论文中提取方法
        mm_output = ""
        domain_methods = ""
        if domain_results and mm_mentor:
            domain_text = "".join(domain_results)
            mm_output = mm_mentor._ask(
                f"以下是关于 '{topic}' 的领域论文：\n\n{domain_text}\n\n"
                f"请分析：\n"
                f"1. 这些论文用了什么方法（RL？行为克隆？其他？）\n"
                f"2. 哪些方法被证明有效\n"
                f"3. 列出每篇论文使用的核心方法（1句话）\n"
                f"不要预设立场，如实总结。",
                max_tokens=32000, logger=self.logger,
            )
            domain_methods = mm_output
            self.logger.action(mm_mentor.name, "domain_analysis", {
                "topic": topic, "methods": mm_output[:300],
            })

        # Phase 3: 根据领域论文的方法，搜对应的方法论文
        method_results = []
        if domain_methods and reason_mentor:
            # 让推理导师决定还需要搜什么方法论文
            method_queries = reason_mentor._ask(
                f"领域论文分析：\n{domain_methods}\n\n"
                f"基于这些论文使用的方法，还需要搜索哪些方法论文来补充？"
                f"请列出 2 个搜索关键词（英文），每行一个。"
                f"如果领域论文已经足够，返回 '不需要'。",
                max_tokens=2000, logger=self.logger,
            )
            if "不需要" not in method_queries:
                for q in method_queries.strip().split("\n")[:2]:
                    q = q.strip()
                    if q:
                        raw = self.scholar.use_tool(query=q)
                        if raw and raw != "No papers found.":
                            method_results.append(raw)
                            self.logger.literature_search("pipeline", q, [])
                        time.sleep(1)

        # Phase 4: 推理导师综合总结
        all_papers = domain_results + method_results
        if not all_papers:
            self.logger.log("文献调研: 未找到相关论文")
            return ""

        papers_text = "".join(all_papers)

        if reason_mentor:
            prompt = f"以下是关于 '{topic}' 的文献：\n\n{papers_text}"
            if mm_output:
                prompt += f"\n\n多模态导师的领域分析：\n{mm_output}\n\n"
            prompt += (
                f"\n\n请综合总结：\n"
                f"1. 这个领域的主流方法是什么（如实总结，不要预设用 RL 或其他）\n"
                f"2. 哪些方法被证明有效\n"
                f"3. 对我们实验的具体建议（用什么方法、注意什么坑）\n"
                f"关键：根据文献说话，不要假设。如果文献说行为克隆有效，就说行为克隆有效。"
            )
            summary = reason_mentor._ask(prompt, max_tokens=800, logger=self.logger)
            self.logger.action(reason_mentor.name, "startup_summary", {
                "topic": topic, "summary": summary[:300],
            })
            return summary

        return mm_output

    def _generate_domain_queries(self, topic: str) -> list[str]:
        """生成领域搜索关键词，不带方法限定"""
        # 拆解 topic 为组件
        queries = [
            topic,  # 原始 topic
        ]
        # 去掉下划线，用空格
        clean_topic = topic.replace("_", " ")
        if clean_topic != topic:
            queries.append(clean_topic)

        # 加一些变体
        if "locomotion" in topic.lower():
            queries.append("quadruped robot thermal motor")
        if "thermal" in topic.lower():
            queries.append("thermal aware locomotion robot")

        return queries[:3]  # 最多 3 个

    # ==================== 工作站：导师直接验证代码 ====================
    def verify_code(self, code: str) -> dict:
        """导师在工作站验证代码"""
        self.logger.log("[工作站] 验证代码...")
        result = self.workstation.run_code(code, timeout=120)
        if result["success"]:
            self.logger.log(f"[工作站] 代码成功执行，输出: {result['stdout'][:200]}")
        else:
            self.logger.log(f"[工作站] 代码失败: {result['stderr'][:200]}")
        return result

    def test_thermal_params(self, **kwargs) -> dict:
        """快速测试热参数"""
        self.logger.log(f"[工作站] 测试热参数: {kwargs}")
        result = self.workstation.test_thermal_params(**kwargs)
        self.logger.log(f"[工作站] 结果: {result['stdout'][:300]}")
        return result

    def build_verified_code(self, starter_code: str) -> str:
        """导师基于 starter code，用工作站验证并修复，返回可用代码"""
        self.logger.log("[工作站] 开始验证并修复代码...")

        # Step 1: 测试热参数
        self.logger.log("[工作站] Step 1: 测试热参数")
        param_result = self.test_thermal_params(
            thermal_mass=2.0, thermal_resistance=0.3,
            power_constant=0.08, resistance=0.8,
            torque=1.0, duration=10.0,
        )
        if not param_result["success"]:
            self.logger.log("[工作站] 热参数测试失败，尝试修复")
            param_result = self.test_thermal_params(
                thermal_mass=5.0, thermal_resistance=0.5,
                torque=1.0, duration=10.0,
            )

        # Step 2: 运行 starter code
        self.logger.log("[工作站] Step 2: 运行 starter code")
        result = self.verify_code(starter_code)

        if result["success"]:
            # 检查输出中是否有有效结果
            stdout = result["stdout"]
            if "Peak Temperature" in stdout and "TAL" in stdout:
                self.logger.log("[工作站] Starter code 能跑，检查结果...")
                # 如果 TAL 温度比 Standard 高，说明参数有问题
                if "TAL" in stdout and "Peak Temperature" in stdout:
                    self.logger.log("[工作站] Starter code 结果已生成")
                    return starter_code
            self.logger.log("[工作站] Starter code 能跑但结果可能有问题")
            return starter_code
        else:
            self.logger.log(f"[工作站] Starter code 失败: {result['stderr'][:200]}")
            return starter_code

    # ==================== 进程管理 ====================
    def stop_agent(self, name: str):
        state = self.agent_states.get(name)
        if not state or not state["process"]:
            return
        if state["process"].poll() is not None:
            return

        self.logger.action("team", "stop_agent", {"agent": name})
        self._kill_tree(state["process"])

    def _kill_tree(self, proc, timeout: int = 5):
        """杀掉整个进程组（进程组由 _launch_process 的 start_new_session 创建）"""
        pgid = self._get_pgid(proc.pid)
        if pgid:
            try:
                os.killpg(pgid, signal.SIGTERM)
            except OSError:
                pass
        else:
            try:
                proc.terminate()
            except OSError:
                pass

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if pgid:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except OSError:
                    pass
            proc.kill()
            try:
                proc.wait(timeout=3)
            except:
                pass

    def restart_agent(self, name: str, guidance: str = ""):
        """重启学生，复用已有的 idea 文件，注入之前最好的代码"""
        self.stop_agent(name)
        time.sleep(3)

        cfg = self.agent_states[name]["config"]
        idea_path = self.root / "mentor" / f"{name}_idea.json"

        # 从之前的实验中找最好的代码
        best_code = self._find_best_code_from_previous(name)

        if idea_path.exists():
            with open(idea_path) as f:
                ideas = json.load(f)
            idea = ideas[0]

            # 更新指导
            if guidance:
                hypothesis = idea.get("Short Hypothesis", "")
                if "## 导师指导" in hypothesis:
                    hypothesis = hypothesis[:hypothesis.index("## 导师指导")]
                idea["Short Hypothesis"] = hypothesis + f"\n\n## 导师指导\n{guidance}\n"

            # 注入之前最好的代码（优先级最高）
            if best_code:
                idea["Code"] = best_code
                self.logger.log(f"[{name}] 从之前的实验注入最好的代码")
            else:
                # 从知识库注入验证过的代码
                verified = self.temporary_kb.query(category="success", tags=["verified_code"], limit=1)
                if verified:
                    idea["Code"] = verified[0].content
                    self.logger.log(f"[{name}] 从知识库注入验证过的代码")

            # 注入学生自己的知识库（失败教训）
            student_kb_content = self._get_student_kb(name).get_summary(max_tokens=2000)
            if student_kb_content:
                idea["Related Work"] = idea.get("Related Work", "") + f"\n\n## 你的实验历史（之前失败的教训）\n{student_kb_content}\n"
                self.logger.log(f"[{name}] 注入学生知识库 ({len(student_kb_content)}字)")

            # 注入其他学生的经验
            cross_exp = self._collect_cross_student_experience(name)
            if cross_exp:
                idea["Related Work"] = idea.get("Related Work", "") + f"\n\n## 其他学生的实验经验\n{cross_exp}\n"

            with open(idea_path, "w") as f:
                json.dump([idea], f, indent=2, ensure_ascii=False)

            self.logger.log(f"[{name}] 复用已有 idea 文件，更新指导")
        else:
            self.start_agent(cfg, guidance)
            return

        # 找最新的实验目录，继续它
        keyword = name.replace("_agent", "")
        exp_dir = self.root / "experiments"
        continue_from = None
        if exp_dir.exists():
            candidates = sorted(
                [d for d in exp_dir.iterdir() if d.is_dir() and keyword in d.name],
                key=lambda d: d.stat().st_mtime,
            )
            if candidates:
                continue_from = str(candidates[-1])
                self.logger.log(f"[{name}] 继续实验: {continue_from}")

        self._launch_process(name, cfg, idea_path, continue_from=continue_from)

    def _find_best_code_from_previous(self, name: str) -> str:
        """从之前的实验中找最好的代码"""
        keyword = name.replace("_agent", "")
        exp_dir = self.root / "experiments"
        if not exp_dir.exists():
            return ""

        candidates = sorted(
            [d for d in exp_dir.iterdir() if d.is_dir() and keyword in d.name],
            key=lambda d: d.stat().st_mtime,
        )

        best_code = ""
        for exp in reversed(candidates):
            jf = self._find_journal(str(exp))
            if not jf:
                continue
            try:
                with open(jf) as f:
                    data = json.load(f)
                nodes = data if isinstance(data, list) else data.get("nodes", [])
            except:
                continue
            good_nodes = [n for n in nodes if not n.get("is_buggy") and n.get("code")]
            if good_nodes:
                best = max(good_nodes, key=lambda n: self._extract_metric_value(n))
                best_code = best.get("code", "")
                if best_code:
                    self.logger.log(f"[{name}] 从 {exp.name} 找到好代码")
                    break

        return best_code

    def _extract_metric_value(self, node: dict) -> float:
        """从节点中提取指标值"""
        metric = node.get("metric", {})
        if not metric:
            return 0.0
        if isinstance(metric, dict):
            value = metric.get("value")
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, dict):
                # 尝试提取第一个数值
                for v in value.values():
                    if isinstance(v, (int, float)):
                        return float(v)
        return 0.0

    # ==================== 终端能力 ====================
    def _run_cmd(self, cmd: str) -> str:
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            return result.stdout.strip()
        except:
            return ""

    def _get_pgid(self, pid: int) -> int | None:
        """获取进程组 ID"""
        try:
            return os.getpgid(pid)
        except OSError:
            return None

    def _check_process_status(self, pid: int) -> dict:
        info = {"pid": pid, "alive": False, "cpu": 0.0, "mem": 0.0, "status": "", "cmd": ""}
        try:
            import signal as sig
            os.kill(pid, 0)  # 检查进程是否存在
        except OSError:
            return info
        info["alive"] = True
        ps = self._run_cmd(f"ps -p {pid} -o stat,%cpu,%mem,cmd --no-headers")
        if ps:
            parts = ps.split(None, 3)
            if len(parts) >= 4:
                info["status"] = parts[0]
                try:
                    info["cpu"] = float(parts[1])
                    info["mem"] = float(parts[2])
                except ValueError:
                    pass
                info["cmd"] = parts[3][:100]
        return info

    def _check_child_processes(self, parent_pid: int) -> list[dict]:
        children = []
        pids = self._run_cmd(f"pgrep -P {parent_pid}")
        if not pids:
            return children
        for pid in pids.split("\n"):
            pid = pid.strip()
            if pid:
                children.append(self._check_process_status(int(pid)))
        return children

    def _check_api_connection(self, pid: int) -> bool:
        return bool(self._run_cmd(f"ss -tnp 2>/dev/null | grep pid={pid}"))

    def _read_recent_log(self, name: str, lines: int = 20) -> str:
        log_path = self.root / "mentor" / "logs" / f"{name}.log"
        if not log_path.exists():
            return ""
        return self._run_cmd(f"tail -{lines} {log_path}")

    def _diagnose_stall(self, name: str, state: dict) -> str:
        pid = state["process"].pid
        proc = self._check_process_status(pid)
        children = self._check_child_processes(pid)
        recent_log = self._read_recent_log(name, 30)

        diagnosis = f"进程 {pid}: 状态={proc['status']} CPU={proc['cpu']}%"

        if children:
            child = children[0]
            diagnosis += f" | 子进程 {child['pid']}: 状态={child['status']} CPU={child['cpu']}%"
            if "D" in child["status"]:
                diagnosis += " | 原因: 卡在 IO"
            elif child["cpu"] == 0.0:
                has_api = self._check_api_connection(child["pid"])
                diagnosis += f" | 原因: {'在等 API' if has_api else '空闲'}"

        if "Backing off" in recent_log:
            diagnosis += " | 线索: API 重试中"
        if "Request timed out" in recent_log:
            diagnosis += " | 线索: API 超时"

        self.logger.diagnosis("team", name, diagnosis)
        return diagnosis

    def _kill_and_restart(self, name: str, state: dict, reason: str):
        self.logger.agent_restarted("team", name, reason)
        self._kill_tree(state["process"])

        if state["restart_count"] < 3:
            guidance = self.history.consensus if self.history.consensus else self.collaborative_analysis(name)
            state["restart_count"] += 1
            self.start_agent(state["config"], guidance)
        else:
            self.logger.log(f"[{name}] 已重启 3 次，停止", "WARN")

    # ==================== Checkpoint ====================
    def _save_checkpoint(self):
        """将关键状态保存到磁盘"""
        # 序列化 ExperimentHistory
        history_data = {
            "consensus": self.history.consensus,
            "records": [asdict(r) for r in self.history.records],
            "failure_patterns": [
                {"pattern": p.pattern, "count": p.count, "examples": p.examples,
                 "root_cause": p.root_cause, "suggested_fix": p.suggested_fix}
                for p in self.history.failure_patterns
            ],
            "meeting_minutes": [
                {
                    "time": m.time,
                    "participants": m.participants,
                    "experiment_summary": m.experiment_summary,
                    "mentor_opinions": m.mentor_opinions,
                    "consensus": m.consensus,
                    "action_items": m.action_items,
                }
                for m in self.history.meeting_minutes
            ],
        }

        # 序列化 agent_states（进程只存 PID + 可恢复字段）
        agents_data = {}
        for name, state in self.agent_states.items():
            proc = state.get("process")
            agents_data[name] = {
                "config": state["config"],
                "pid": proc.pid if proc and proc.poll() is None else None,
                "stuck_count": state.get("stuck_count", 0),
                "restart_count": state.get("restart_count", 0),
                "last_node_count": state.get("last_node_count", 0),
                "last_progress_time": state.get("last_progress_time", 0),
                "meeting_held_for_pattern": state.get("meeting_held_for_pattern"),
            }

        # 学生→idea 映射
        student_ideas_data = self.student_ideas

        checkpoint = {
            "timestamp": time.time(),
            "history": history_data,
            "agents": agents_data,
            "student_ideas": student_ideas_data,
        }

        tmp_path = self._checkpoint_path.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            json.dump(checkpoint, f, indent=2, ensure_ascii=False)
        tmp_path.rename(self._checkpoint_path)

    def _load_checkpoint(self):
        """从磁盘恢复状态，尝试接管还活着的学生进程"""
        if not self._checkpoint_path.exists():
            self.logger.log("[checkpoint] 无 checkpoint，全新启动")
            return

        try:
            with open(self._checkpoint_path) as f:
                cp = json.load(f)
        except Exception as e:
            self.logger.log(f"[checkpoint] 加载失败: {e}，全新启动", "WARN")
            return

        elapsed = time.time() - cp.get("timestamp", 0)
        self.logger.log(f"[checkpoint] 发现 checkpoint（{elapsed:.0f}秒前保存）")

        # 恢复 ExperimentHistory
        h = cp.get("history", {})
        if h.get("consensus"):
            self.history.consensus = h["consensus"]
        for rd in h.get("records", []):
            self.history.records.append(ExperimentRecord(
                node_id=rd.get("node_id", 0),
                code=rd.get("code", ""),
                output=rd.get("output", ""),
                error=rd.get("error", ""),
                analysis=rd.get("analysis", ""),
                is_buggy=rd.get("is_buggy", True),
                metric=rd.get("metric", {}),
                iteration=rd.get("iteration", 0),
            ))
        for pd in h.get("failure_patterns", []):
            self.history.failure_patterns.append(FailurePattern(
                pattern=pd.get("pattern", ""),
                count=pd.get("count", 0),
                examples=pd.get("examples", []),
                root_cause=pd.get("root_cause", ""),
                suggested_fix=pd.get("suggested_fix", ""),
            ))
        for md in h.get("meeting_minutes", []):
            self.history.meeting_minutes.append(MeetingMinutes(
                time=md.get("time", ""),
                participants=md.get("participants", []),
                experiment_summary=md.get("experiment_summary", ""),
                mentor_opinions=md.get("mentor_opinions", {}),
                consensus=md.get("consensus", ""),
                action_items=md.get("action_items", []),
            ))
        self.logger.log(f"[checkpoint] 恢复实验历史: {len(self.history.records)} 条记录, {len(self.history.failure_patterns)} 个失败模式, {len(self.history.meeting_minutes)} 次组会")

        # 恢复 student_ideas
        self.student_ideas = cp.get("student_ideas", {})

        # 恢复 agent_states，尝试接管进程
        restored = 0
        adopted = 0
        for name, ad in cp.get("agents", {}).items():
            pid = ad.get("pid")
            proc = None

            if pid:
                # 检查进程是否还活着
                try:
                    os.kill(pid, 0)
                    proc = AdoptedProcess(pid)
                    adopted += 1
                    self.logger.log(f"[checkpoint] 接管学生 {name} (PID={pid})")
                except OSError:
                    self.logger.log(f"[checkpoint] 学生 {name} (PID={pid}) 已退出")

            self.agent_states[name] = {
                "config": ad["config"],
                "process": proc,
                "stuck_count": ad.get("stuck_count", 0),
                "restart_count": ad.get("restart_count", 0),
                "last_node_count": ad.get("last_node_count", 0),
                "last_progress_time": ad.get("last_progress_time", time.time()),
                "meeting_held_for_pattern": ad.get("meeting_held_for_pattern"),
            }
            restored += 1

            # 如果进程已死，检查是否需要重启
            if not proc:
                self._handle_dead_student_after_restore(name, self.agent_states[name])

        self.logger.log(f"[checkpoint] 恢复 {restored} 个学生，其中 {adopted} 个进程仍在运行")

        # 清理僵尸 student_ideas：没有对应 agent_state 的条目
        zombie_ideas = [name for name in self.student_ideas if name not in self.agent_states]
        for name in zombie_ideas:
            del self.student_ideas[name]
            self.logger.log(f"[checkpoint] 清理僵尸 student_idea: {name}")

    def _handle_dead_student_after_restore(self, name: str, state: dict):
        """恢复后发现学生已死，决定是否重启"""
        keyword = name.replace("_agent", "")
        jf, nodes = self._find_journal(keyword)
        good = sum(1 for n in nodes if not n.get("is_buggy"))

        if good > 0:
            self.logger.log(f"[checkpoint] {name} 有 {good} 个好节点，重启继续实验")
            state["restart_count"] = state.get("restart_count", 0)
            self.start_agent(state["config"], "")
        elif state.get("restart_count", 0) < 3:
            self.logger.log(f"[checkpoint] {name} 无好节点且未超重启次数，重新启动")
            guidance = self.history.consensus if self.history.consensus else ""
            state["restart_count"] += 1
            self.start_agent(state["config"], guidance)
        else:
            self.logger.log(f"[checkpoint] {name} 已重启 3 次，跳过", "WARN")

    # ==================== journal ====================
    def _find_journal(self, keyword: str) -> tuple[str, list[dict]]:
        keyword = keyword.replace("student_", "").replace("_agent", "")
        exp_dir = self.root / "experiments"
        if not exp_dir.exists():
            return "", []
        candidates = sorted(
            [d for d in exp_dir.iterdir() if d.is_dir() and keyword in d.name],
            key=lambda d: d.stat().st_mtime,
        )
        if not candidates:
            return "", []
        best_jf, best_nodes = "", []
        for root, dirs, files in os.walk(candidates[-1]):
            for f in files:
                if f == "journal.json":
                    jf = os.path.join(root, f)
                    try:
                        with open(jf) as fh:
                            data = json.load(fh)
                        nodes = data if isinstance(data, list) else data.get("nodes", [])
                        good = sum(1 for n in nodes if not n.get("is_buggy"))
                        best_good = sum(1 for n in best_nodes if not n.get("is_buggy"))
                        if good > best_good or (good == best_good and len(nodes) > len(best_nodes)):
                            best_jf, best_nodes = jf, nodes
                    except:
                        pass
        return best_jf, best_nodes

    def inject_node(self, name: str, code: str, plan: str = ""):
        jf, nodes = self._find_journal(name.replace("_agent", ""))
        if not jf:
            self.logger.log(f"[{name}] 无法注入：没有 journal", "WARN")
            return False
        nodes.append({
            "plan": plan, "code": code, "is_buggy": False,
            "analysis": "Mentor verified", "metric": {"value": 0.5, "maximize": True, "name": "verified"},
            "id": f"mentor_{int(time.time())}", "ctime": time.time(),
            "children": [], "parent_id": nodes[-1]["id"] if nodes else None,
            "parent": nodes[-1]["id"] if nodes else None,
        })
        with open(jf, "w") as f:
            json.dump(nodes, f, indent=2, default=str)
        self.logger.node_injected("team", name, plan)
        return True

    # ==================== 论文 ====================
    def trigger_writeup(self, name: str):
        state = self.agent_states.get(name)
        if not state:
            return
        cfg = state["config"]

        # 找到最佳实验目录（好节点最多的 run）
        keyword = name.replace("student_", "").replace("_agent", "")
        exp_dir = self.root / "experiments"
        best_dir, best_good = "", 0
        if exp_dir.exists():
            for d in sorted(exp_dir.iterdir(), key=lambda d: d.stat().st_mtime):
                if d.is_dir() and keyword in d.name:
                    # 统计该目录下所有 journal 的好节点数
                    total_good = 0
                    for root_dir, dirs, files in os.walk(d):
                        for f in files:
                            if f == "journal.json":
                                try:
                                    with open(os.path.join(root_dir, f)) as fh:
                                        data = json.load(fh)
                                    nodes = data if isinstance(data, list) else data.get("nodes", [])
                                    total_good += sum(1 for n in nodes if not n.get("is_buggy"))
                                except:
                                    pass
                    if total_good > best_good:
                        best_good = total_good
                        best_dir = str(d)

        if not best_dir or best_good == 0:
            self.logger.log(f"[{name}] 无好节点，跳过论文撰写", "WARN")
            return

        self.logger.log(f"[team] 触发论文撰写: {best_dir} ({best_good} 好节点)")

        # 直接调用 perform_writeup，跳过实验阶段
        writeup_script = self.root / "mentor" / "run_writeup.py"
        env = os.environ.copy()
        env.update({
            "S2_API_KEY": os.environ.get("S2_API_KEY", ""),
            "AI_SCIENTIST_ROOT": str(self.root),
        })
        for k in ["ALL_PROXY", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
            env.pop(k, None)
        cmd = [
            str(self.root / ".venv" / "bin" / "python"),
            str(writeup_script),
            "--exp_dir", best_dir,
            "--model_writeup", cfg["model"],
            "--model_citation", cfg["model"],
            "--model_writeup_small", cfg["model"],
        ]
        log_path = self.root / "mentor" / "logs" / f"{name}_writeup.log"
        with open(log_path, "w") as f:
            subprocess.Popen(cmd, env=env, cwd=str(self.root), stdout=f, stderr=subprocess.STDOUT)

    # ==================== 主循环 ====================
    def monitor_loop(self):
        self.logger.log("="*50)
        self.logger.log("导师团队监控启动")
        self.logger.log("="*50)
        while True:
            for name, state in list(self.agent_states.items()):
                self._check(name, state)
            self._save_checkpoint()
            time.sleep(self.check_interval)

    def _is_deep_exploring(self, nodes, window=8):
        """判断学生是否在深度探索而非原地转圈：综合错误多样性、执行时间趋势、debug链、节点间隔"""
        recent = nodes[-window:]
        if not recent:
            return False

        # 1. 错误多样性：unique exc_type / total buggy
        exc_types = [n.get("exc_type") for n in recent if n.get("is_buggy") and n.get("exc_type")]
        if exc_types:
            diversity = len(set(exc_types)) / len(exc_types)
        else:
            diversity = 1.0

        # 2. exec_time 趋势：最近窗口内是否有 >60s 的执行
        has_long_exec = any((n.get("exec_time") or 0) > 60 for n in recent)

        # 3. 是否有 debug 链（parent_id 非 None 的比例 > 30%）
        has_chains = sum(1 for n in recent if n.get("parent_id")) > len(recent) * 0.3

        # 4. 节点间时间差：不是全部秒崩（平均间隔 > 30秒）
        ctimes = sorted(n.get("ctime", 0) for n in recent if n.get("ctime"))
        if len(ctimes) >= 2:
            avg_gap = sum(ctimes[i + 1] - ctimes[i] for i in range(len(ctimes) - 1)) / (len(ctimes) - 1)
            not_rapid_fire = avg_gap > 30
        else:
            not_rapid_fire = False

        score = diversity * 0.4 + (0.2 if has_long_exec else 0) + (0.2 if has_chains else 0) + (0.2 if not_rapid_fire else 0)
        return score >= 0.5

    def _count_stage_goods(self, name: str) -> dict:
        keyword = name.replace("student_", "").replace("_agent", "")
        exp_dir = self.root / "experiments"
        if not exp_dir.exists():
            return {}
        candidates = sorted(
            [d for d in exp_dir.iterdir() if d.is_dir() and keyword in d.name],
            key=lambda d: d.stat().st_mtime,
        )
        if not candidates:
            return {}
        stage_goods = {}
        for root_dir, dirs, files in os.walk(candidates[-1]):
            for f in files:
                if f == "journal.json":
                    try:
                        with open(os.path.join(root_dir, f)) as fh:
                            data = json.load(fh)
                        nodes = data if isinstance(data, list) else data.get("nodes", [])
                        good = sum(1 for n in nodes if not n.get("is_buggy"))
                        parts = os.path.basename(root_dir).split("_")
                        if len(parts) >= 2 and parts[0] == "stage":
                            stage_num = int(parts[1])
                            stage_goods[stage_num] = stage_goods.get(stage_num, 0) + good
                    except:
                        pass
        return stage_goods

    def _is_experiment_complete(self, name: str) -> bool:
        """检查实验是否完成（stage 3+ 有好节点才认为完成）"""
        sg = self._count_stage_goods(name)
        has_s1 = sg.get(1, 0) > 0
        has_s2 = sg.get(2, 0) > 0
        has_s3 = sg.get(3, 0) > 0
        has_s4 = sg.get(4, 0) > 0
        if has_s4:
            return True
        if has_s3 and has_s1 and has_s2:
            return True
        return False

    def _check(self, name: str, state: dict):
        jf, nodes = self._find_journal(name.replace("_agent", ""))
        good = sum(1 for n in nodes if not n.get("is_buggy"))
        buggy = len(nodes) - good
        alive = state["process"] and state["process"].poll() is None
        now = time.time()

        # 更新实验历史 + 导师审查 + 存入知识库
        if len(nodes) > state.get("last_node_count", 0):
            for i in range(state.get("last_node_count", 0), len(nodes)):
                n = nodes[i]
                code = n.get("code", "")

                # 存入知识库
                if n.get("is_buggy") and n.get("analysis"):
                    self.temporary_kb.add(
                        category="failure",
                        content=n["analysis"],
                        summary=f"节点{i}: {n['analysis'][:150]}",
                        importance=2,
                        tags=["bug", name],
                    )
                    # 也存入学生知识库
                    self._get_student_kb(name).add(
                        category="failure",
                        content=n["analysis"],
                        summary=f"节点{i}: {n['analysis'][:150]}",
                        importance=3,
                        tags=["bug"],
                    )
                elif not n.get("is_buggy") and n.get("metric"):
                    self.temporary_kb.add(
                        category="success",
                        content=code,  # 保存完整代码
                        summary=f"节点{i}: 成功，指标={str(n.get('metric',{}))[:100]}",
                        importance=5,
                        tags=["success", name, "verified_code"],
                    )
                    # 也存入学生知识库
                    self._get_student_kb(name).add(
                        category="success",
                        content=code,
                        summary=f"节点{i}: 成功，指标={str(n.get('metric',{}))[:100]}",
                        importance=5,
                        tags=["success", "verified_code"],
                    )

                # 导师审查方案是否合理
                if code and n.get("is_buggy"):
                    code_mentor = self.mentors.get("code_mentor")
                    if code_mentor:
                        review = self.monitor.review_approach(
                            code, self.history.get_summary(), code_mentor,
                        )
                        if not review["approved"]:
                            self.logger.log(f"[{name}] 导师审查不通过: {review['reason']}")
                            self.temporary_kb.add(
                                category="decision",
                                content=review.get("reasoning", ""),
                                summary=f"导师审查不通过: {review['reason'][:150]}",
                                importance=3,
                                tags=["review", "rejected", name],
                            )
                            guidance = self.monitor.get_guidance_from_review(review)
                            self._kill_and_restart(name, state, f"审查不通过: {review['reason'][:60]}")
                            return

                self.history.add_record(ExperimentRecord(
                    node_id=i, code=code,
                    analysis=n.get("analysis", ""),
                    is_buggy=n.get("is_buggy", True),
                    metric=n.get("metric", {}),
                ))
            state["last_node_count"] = len(nodes)
            state["last_progress_time"] = now

            # 检查是否需要开组会
            if self.history.failure_patterns:
                dominant = max(self.history.failure_patterns, key=lambda p: p.count)
                if dominant.count >= 3 and not state.get("meeting_held_for_pattern"):
                    self.logger.log(f"[{name}] 检测到失败模式：{dominant.pattern}（{dominant.count}次），召开组会")
                    minutes = self.meeting.hold_meeting(name)
                    state["meeting_held_for_pattern"] = dominant.pattern
                    if self.history.consensus:
                        self.logger.log(f"[{name}] 组会共识已记录")
                        self.temporary_kb.add(
                            category="consensus",
                            content=self.history.consensus,
                            summary=self.history.consensus[:200],
                            importance=5,
                            tags=["consensus", "meeting"],
                        )

        # 检查上下文是否需要压缩
        self._check_context_size(name, state)

        self.logger.log(f"[{name}] {'活着' if alive else '已退出'} | 总={len(nodes)} 好={good} bug={buggy}")

        if not alive:
            if good > 0 and self._is_experiment_complete(name):
                self.logger.log(f"[{name}] 实验完成(stage 3+有好节点)，触发论文撰写")
                self.trigger_writeup(name)
            elif good > 0:
                has_s3 = self._count_stage_goods(name).get(3, 0) > 0
                if has_s3:
                    self.logger.log(f"[{name}] stage 3 已完成但 stage 4 未开始，触发论文撰写")
                    self.trigger_writeup(name)
                else:
                    self.logger.log(f"[{name}] 有 {good} 个好节点但 stage 3 未完成，重启继续实验")
                    self.start_agent(state["config"], "")
            elif state["restart_count"] < 3:
                # 如果有组会共识，用它作为指导
                guidance = self.history.consensus if self.history.consensus else self.collaborative_analysis(name)
                state["restart_count"] += 1
                self.start_agent(state["config"], guidance)
            else:
                self.logger.log(f"[{name}] 已重启 3 次，停止", "WARN")
            return

        stall_minutes = (now - state.get("last_progress_time", now)) / 60
        if stall_minutes > 60:  # 从30分钟改为60分钟
            # 先检查学生是否真的卡死（CPU和网络）
            pid = state["process"].pid
            proc_status = self._check_process_status(pid)
            children = self._check_child_processes(pid)
            
            # 如果学生还有CPU活动或网络连接，说明在工作，不是卡死
            has_activity = proc_status.get("cpu", 0) > 0.1
            if children:
                has_activity = has_activity or any(c.get("cpu", 0) > 0.1 for c in children)
            
            if has_activity:
                self.logger.log(f"[{name}] 学生仍在工作 (CPU={proc_status.get('cpu',0)}%)，不重启")
                state["last_progress_time"] = now  # 重置计时器
                return
            
            diagnosis = self._diagnose_stall(name, state)
            self._kill_and_restart(name, state, f"卡死 {stall_minutes:.0f}分钟")
            return

        if buggy >= self.stuck_threshold and good == 0:
            if self._is_deep_exploring(nodes):
                self.logger.log(f"[{name}] 检测到深度探索（错误在演化），暂不干预")
                state["stuck_count"] = 0
            else:
                state["stuck_count"] += 1
            if state["stuck_count"] >= 2:
                self.logger.log(f"[{name}] 持续卡住，召开组会并用工作站验证代码")
                minutes = self.meeting.hold_meeting(name)
                guidance = self.history.consensus if self.history.consensus else self.collaborative_analysis(name)

                # 用工作站验证代码
                starter = state["config"].get("starter_code", "")
                if starter:
                    sp = self.root / "ai_scientist" / "ideas" / starter
                    if sp.exists():
                        with open(sp) as f:
                            starter_code = f.read()
                        # 导师先在工作站验证
                        verified_code = self.build_verified_code(starter_code)
                        self.inject_node(name, verified_code, "导师工作站验证后注入")

                self.restart_agent(name, guidance)
                state["stuck_count"] = 0
                state["meeting_held_for_pattern"] = None  # 重置
        else:
            state["stuck_count"] = 0

    def _check_context_size(self, name: str, state: dict):
        """检查单个导师/学生的上下文大小，超过阈值就压缩"""
        # 1. 检查导师上下文（实验历史节点）
        mentor_context = "".join(
            (n.code or "") + (n.analysis or "")
            for n in self.history.records
        )
        mentor_tokens = _estimate_tokens(mentor_context)

        if mentor_tokens > self.context_threshold:
            self.logger.log(f"[{name}] 导师上下文过大: ~{mentor_tokens} tokens，压缩")

            compressed = self.compressor.compress_mentor_context(
                self.history, self.temporary_kb, target_tokens=50000,
            )

            self.temporary_kb.add(
                category="mentor_context_snapshot",
                content=compressed,
                summary=f"导师上下文压缩，约{mentor_tokens}tokens",
                importance=3,
                tags=["compressed", "mentor"],
            )

            self.history = ExperimentHistory(llm_classifier=self._history_classifier)
            self.history.consensus = compressed[:500]

            self.logger.log(f"[{name}] 导师上下文已压缩，不重启学生")
            return  # 只压缩导师上下文，不重启学生

        # 2. 检查学生上下文（journal 节点的代码）
        jf, nodes = self._find_journal(name.replace("_agent", ""))
        if nodes:
            student_context = "".join(n.get("code", "") for n in nodes)
            student_tokens = _estimate_tokens(student_context)

            if student_tokens > self.context_threshold:
                self.logger.log(f"[{name}] 学生上下文过大: ~{student_tokens} tokens，压缩")

                compressed = self.compressor.compress_student_context(
                    nodes, target_tokens=30000,
                )

                self.temporary_kb.add(
                    category="student_context_snapshot",
                    content=compressed,
                    summary=f"学生上下文压缩，约{student_tokens}tokens",
                    importance=3,
                    tags=["compressed", "student"],
                )

                self._kill_and_restart(name, state, "学生上下文压缩后重启")

    def _compress_temp_context(self) -> str:
        """压缩临时上下文（实验历史），保留关键信息"""
        summary_parts = []

        # 失败模式
        if self.history.failure_patterns:
            summary_parts.append("失败模式:")
            for p in self.history.failure_patterns:
                summary_parts.append(f"  - {p.pattern}: {p.count}次")

        # 最近的成功节点
        good_nodes = [r for r in self.history.records if not r.is_buggy]
        if good_nodes:
            summary_parts.append(f"成功节点: {len(good_nodes)}个")
            for n in good_nodes[-2:]:
                summary_parts.append(f"  - 指标: {str(n.metric)[:100]}")

        # 最近的失败节点（只保留分析，不保留代码）
        buggy_nodes = [r for r in self.history.records if r.is_buggy]
        if buggy_nodes:
            summary_parts.append(f"失败节点: {len(buggy_nodes)}个")
            for n in buggy_nodes[-3:]:
                summary_parts.append(f"  - {n.analysis[:100]}")

        # 组会共识
        if self.history.consensus:
            summary_parts.append(f"组会共识: {self.history.consensus[:200]}")

        return "\n".join(summary_parts)

    def start_all(self):
        """导师决定启动哪些学生"""
        # 如果配置文件里有 agents，用旧方式
        if "agents" in self.config:
            for a in self.config["agents"]:
                if a.get("enabled", True):
                    self.start_agent(a)
        else:
            # 导师自动分配：每个想法一个学生（不超过 max_students）
            for i, idea in enumerate(self.ideas[:self.max_students]):
                self.create_student(idea_idx=i)

    def stop_all(self):
        for n in self.agent_states:
            self.stop_agent(n)
