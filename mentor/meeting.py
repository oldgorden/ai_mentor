"""组会：导师团队 + 学生共享实验历史，讨论并达成共识
改进：
1. LLM 语义错误分类（替代硬编码关键词）
2. 并行导师发言（ThreadPoolExecutor）
3. 结构化投票共识（多数派直接取，分歧时才仲裁）
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))


@dataclass
class ExperimentRecord:
    """单次实验记录"""
    node_id: int
    code: str = ""
    output: str = ""
    error: str = ""
    analysis: str = ""
    is_buggy: bool = True
    metric: dict = field(default_factory=dict)
    iteration: int = 0


@dataclass
class FailurePattern:
    """失败模式"""
    pattern: str
    count: int = 0
    examples: list = field(default_factory=list)
    root_cause: str = ""
    suggested_fix: str = ""


@dataclass
class MentorVote:
    """导师结构化投票"""
    mentor_name: str = ""
    method_choice: str = ""
    confidence: float = 0.0
    reasoning: str = ""
    action: str = ""


@dataclass
class MeetingMinutes:
    """组会纪要"""
    time: str = ""
    participants: list = field(default_factory=list)
    experiment_summary: str = ""
    failure_patterns: list = field(default_factory=list)
    mentor_opinions: dict = field(default_factory=dict)
    mentor_votes: list = field(default_factory=list)
    consensus: str = ""
    action_items: list = field(default_factory=list)


class ExperimentHistory:
    """共享实验历史（支持 LLM 语义分类）"""

    def __init__(self, llm_classifier=None):
        self.records: list[ExperimentRecord] = []
        self.failure_patterns: list[FailurePattern] = []
        self.meeting_minutes: list[MeetingMinutes] = []
        self.consensus: str = ""
        self._llm_classifier = llm_classifier
        self._pattern_labels: dict[str, str] = {}
        self._llm_labeled_keys: set = set()

    def add_record(self, record: ExperimentRecord):
        self.records.append(record)
        self._detect_patterns()

    # ==================== 错误分类 ====================

    def _classify_error(self, analysis: str) -> str:
        """通用初始分组（不绑定任何领域），仅做粗粒度聚类"""
        a = analysis.lower()[:500]
        if any(k in a for k in ("dimension", "shape", "mismatch", "size")):
            return "__dim_mismatch"
        if "assertion" in a or "assert " in a:
            return "__assertion_fail"
        if "timeout" in a or "timed out" in a:
            return "__timeout"
        if any(k in a for k in ("import", "no module", "module not found")):
            return "__import_error"
        if any(k in a for k in ("syntax", "parse error", "unexpected")):
            return "__syntax_error"
        if any(k in a for k in ("keyerror", "attributeerror", "typeerror", "valueerror")):
            return "__runtime_exception"
        if "nan" in a or "inf" in a:
            return "__numerical_instability"
        words = [w for w in a.split()[:8] if len(w) > 3 and w.isalpha()]
        return "__generic_" + "_".join(words[:3]) if words else "__unknown"

    def _detect_patterns(self):
        """检测失败模式，初始分组 + LLM 精细标注"""
        error_groups: dict[str, list] = {}
        for r in self.records:
            if not r.is_buggy or not r.analysis:
                continue
            key = self._classify_error(r.analysis)
            if key not in error_groups:
                error_groups[key] = []
            error_groups[key].append(r.node_id)

        self.failure_patterns = []
        for key, nodes in error_groups.items():
            if len(nodes) < 2:
                continue
            label = self._pattern_labels.get(key, key.lstrip("_").replace("_", " "))
            self.failure_patterns.append(FailurePattern(
                pattern=label,
                count=len(nodes),
                examples=nodes,
            ))

        if self._llm_classifier:
            self._refine_patterns_with_llm(error_groups)

    def _refine_patterns_with_llm(self, error_groups: dict):
        """对未标注的模式用 LLM 做一次精准命名，每种模式只调一次"""
        for key, nodes in error_groups.items():
            if len(nodes) < 2:
                continue
            if key in self._llm_labeled_keys:
                continue

            samples = []
            for r in self.records:
                if r.node_id in nodes[:3] and r.analysis:
                    samples.append(r.analysis[:200])
            if not samples:
                continue

            label = self._llm_classify_samples(samples)
            if label:
                self._pattern_labels[key] = label
                for fp in self.failure_patterns:
                    if set(fp.examples) & set(nodes):
                        fp.pattern = label
            self._llm_labeled_keys.add(key)

    def _llm_classify_samples(self, samples: list[str]) -> str:
        """调 LLM 对错误样本分类，返回简短标签"""
        if not self._llm_classifier:
            return ""
        try:
            prompt = (
                "以下是实验中重复出现的错误分析样本。"
                "请用一句简短的中文描述失败模式的本质（不超过20字）。\n\n"
                + "\n---\n".join(samples)
                + "\n\n失败模式标签："
            )
            result = self._llm_classifier._ask(prompt, max_tokens=200, deep_think=False)
            label = result.strip().split("\n")[0].strip()
            label = label.strip('"\'""''').lstrip("标签：: ")
            return label[:50] if label else ""
        except Exception:
            return ""

    # ==================== 摘要 ====================

    def get_summary(self) -> str:
        total = len(self.records)
        good = sum(1 for r in self.records if not r.is_buggy)
        buggy = total - good
        lines = [f"实验历史：共 {total} 个节点，{good} 个成功，{buggy} 个失败"]
        if self.failure_patterns:
            lines.append("\n失败模式（重复出现的问题）：")
            for p in sorted(self.failure_patterns, key=lambda x: -x.count):
                lines.append(f"  - {p.pattern}: 出现 {p.count} 次（节点 {p.examples}）")
        if self.consensus:
            lines.append(f"\n当前共识：{self.consensus}")
        return "\n".join(lines)

    def get_successful_code(self) -> str:
        for r in reversed(self.records):
            if not r.is_buggy and r.code:
                return r.code
        return ""

    def get_failed_codes_with_reasons(self) -> str:
        lines = []
        for r in self.records:
            if r.is_buggy and r.analysis:
                lines.append(f"节点 {r.node_id}: {r.analysis[:150]}")
        return "\n".join(lines[-5:])


class GroupMeeting:
    """组会：并行发言 + 结构化投票 + 大导师裁决"""

    def __init__(self, mentors: dict, history: ExperimentHistory, logger=None, chief_mentor=None):
        self.mentors = mentors
        self.history = history
        self.logger = logger
        self.chief_mentor = chief_mentor

    def hold_meeting(self, agent_name: str) -> MeetingMinutes:
        if self.logger:
            self.logger.log(f"[组会] 开始，参与者: {list(self.mentors.keys())}")

        minutes = MeetingMinutes(
            time=time.strftime("%Y-%m-%d %H:%M:%S"),
            participants=list(self.mentors.keys()),
            experiment_summary=self.history.get_summary(),
        )

        if self.logger:
            self.logger.log(f"[组会] 实验摘要:\n{minutes.experiment_summary}")

        minutes.mentor_opinions, minutes.mentor_votes = self._parallel_speak(agent_name)

        minutes.consensus = self._reach_consensus(minutes.mentor_opinions, minutes.mentor_votes)
        self.history.consensus = minutes.consensus

        minutes.action_items = self._determine_actions(minutes)
        self.history.meeting_minutes.append(minutes)

        if self.logger:
            self.logger.log(f"[组会] 共识: {minutes.consensus[:200]}")
            self.logger.log(f"[组会] 行动项: {minutes.action_items}")

        return minutes

    # ==================== 并行发言 ====================

    def _parallel_speak(self, agent_name: str) -> tuple[dict, list[MentorVote]]:
        """并行调所有导师发言"""
        opinions: dict[str, str] = {}
        votes: list[MentorVote] = []

        def _speak_one(item):
            name, mentor = item
            return name, *self._mentor_speak(mentor, agent_name)

        with ThreadPoolExecutor(max_workers=len(self.mentors)) as pool:
            futures = {pool.submit(_speak_one, item): item[0]
                       for item in self.mentors.items()}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    _, opinion, vote = future.result()
                    opinions[name] = opinion
                    if vote:
                        votes.append(vote)
                    if self.logger:
                        self.logger.action(name, "meeting_opinion", {
                            "agent": agent_name, "opinion": opinion[:300],
                        })
                except Exception as e:
                    if self.logger:
                        self.logger.log(f"[组会] {name} 发言失败: {e}", "WARN")
                    opinions[name] = f"发言失败: {e}"

        return opinions, votes

    def _mentor_speak(self, mentor, agent_name: str) -> tuple[str, MentorVote]:
        """单个导师发言（含结构化投票）"""
        context = f"""你正在参加实验组会。以下是实验历史：

{self.history.get_summary()}

最近失败的节点：
{self.history.get_failed_codes_with_reasons()}

成功的代码（如果有）：
{self.history.get_successful_code()[:1000] if self.history.get_successful_code() else '无'}
"""

        vote_instruction = """

在分析的最后，请严格按以下格式给出你的投票：

VOTE_START
METHOD: 你推荐的方法（一句话）
CONFIDENCE: 0.0到1.0之间的置信度
ACTION: 具体的下一步行动（一句话）
VOTE_END
"""

        if "critical" in mentor.name:
            prompt = f"""{context}

作为批判性思维导师，请**逐步推理**：

**Step 1: 识别模式** - 失败的节点有什么共同点？说明了什么？
**Step 2: 质疑假设** - 团队目前做了哪些没有验证的假设？可能是错的吗？
**Step 3: 提出替代方案** - 有没有被忽略的更简单方法？
**Step 4: 给出具体建议** - 下一步应该做什么？（具体到代码层面）
{vote_instruction}"""

        elif "code" in mentor.name:
            prompt = f"""{context}

作为代码导师，请**逐步推理**：

**Step 1: 分析代码逻辑** - 代码做了什么？用了什么方法？
**Step 2: 推理方法可行性** - 这个方法能达到实验目标吗？
**Step 3: 分析参数** - 参数设置是否合理？
**Step 4: 给出具体修改** - 具体改哪一行？改成什么？
{vote_instruction}"""

        elif "reasoning" in mentor.name:
            prompt = f"""{context}

作为推理导师，请**逐步推理**：

**Step 1: 总结规律** - 从实验历史中能看到什么规律？
**Step 2: 判断方向** - 当前方向是否值得继续？
**Step 3: 预测后果** - 继续当前方法 vs 换方法，分别会怎样？
**Step 4: 给出建议** - 下一步应该做什么？
{vote_instruction}"""

        elif "multimodal" in mentor.name:
            prompt = f"""{context}

作为文献导师，请**逐步推理**：

**Step 1: 匹配文献** - 实验中的问题，文献里有没有解决方案？
**Step 2: 提取方法** - 相关文献用了什么方法？哪些被证明有效？
**Step 3: 评估适用性** - 这些方法适用于当前实验吗？需要什么调整？
**Step 4: 给出建议** - 应该参考哪些文献？怎么用？
{vote_instruction}"""

        else:
            prompt = f"{context}\n\n请对当前实验进展发表看法。简洁。\n{vote_instruction}"

        try:
            response = mentor._ask(prompt, max_tokens=64000, deep_think=True)
            vote = self._parse_vote(response, mentor.name)
            return response, vote
        except Exception:
            return "发言失败", MentorVote(mentor_name=mentor.name)

    def _parse_vote(self, response: str, mentor_name: str) -> MentorVote:
        """从导师发言中提取结构化投票"""
        vote = MentorVote(mentor_name=mentor_name, reasoning=response[:300])

        match = re.search(r"VOTE_START\s*\n(.*?)VOTE_END", response, re.DOTALL)
        if not match:
            return vote

        block = match.group(1)
        method_m = re.search(r"METHOD:\s*(.+)", block)
        conf_m = re.search(r"CONFIDENCE:\s*([\d.]+)", block)
        action_m = re.search(r"ACTION:\s*(.+)", block)

        if method_m:
            vote.method_choice = method_m.group(1).strip()
        if conf_m:
            try:
                vote.confidence = float(conf_m.group(1))
            except ValueError:
                vote.confidence = 0.5
        if action_m:
            vote.action = action_m.group(1).strip()

        return vote

    # ==================== 共识机制 ====================

    def _reach_consensus(self, opinions: dict[str, str], votes: list[MentorVote]) -> str:
        """大导师始终裁决：收集投票预分析，连同批判性意见一起交给大导师"""
        valid_votes = [v for v in votes if v.method_choice]
        critical_opinion = opinions.get("critical_mentor", "")

        # 投票预分析
        vote_analysis = self._analyze_votes(valid_votes)

        # 大导师裁决（始终调用）
        return self._chief_synthesize(opinions, valid_votes, vote_analysis, critical_opinion)

    def _analyze_votes(self, valid_votes: list[MentorVote]) -> dict:
        """预分析投票分布，给大导师参考（不替代大导师判断）"""
        if not valid_votes:
            return {"status": "no_votes", "summary": "导师未提供有效投票"}

        method_groups: dict[str, list[MentorVote]] = {}
        for v in valid_votes:
            matched = False
            for existing_key in method_groups:
                if self._methods_similar(v.method_choice, existing_key):
                    method_groups[existing_key].append(v)
                    matched = True
                    break
            if not matched:
                method_groups[v.method_choice] = [v]

        sorted_groups = sorted(method_groups.items(), key=lambda x: -len(x[1]))

        avg_conf = sum(v.confidence for v in valid_votes) / len(valid_votes)

        if len(sorted_groups) == 1:
            status = "unanimous"
            summary = f"全体一致推荐：{sorted_groups[0][0]}"
        elif len(sorted_groups[0][1]) > len(valid_votes) / 2:
            status = "majority"
            majority = sorted_groups[0]
            minority_names = [v.mentor_name for k, vs in sorted_groups[1:] for v in vs]
            summary = (f"多数派 ({len(majority[1])}/{len(valid_votes)}) 推荐：{majority[0]}，"
                       f"少数派：{minority_names}")
        else:
            status = "divided"
            summary = "导师意见严重分歧：" + " vs ".join(
                f"{k}({len(vs)}票)" for k, vs in sorted_groups
            )

        return {
            "status": status,
            "summary": summary,
            "avg_confidence": avg_conf,
            "groups": {k: [v.mentor_name for v in vs] for k, vs in sorted_groups},
        }

    _STOPWORDS = frozenset(
        "use using the a an to for with of in on is it and or by from as be that this".split()
    )

    def _methods_similar(self, a: str, b: str) -> bool:
        """判断两个方法描述是否实质相同（过滤常用词后比较）"""
        a_words = set(a.lower().split()) - self._STOPWORDS
        b_words = set(b.lower().split()) - self._STOPWORDS
        if not a_words or not b_words:
            return a.strip().lower() == b.strip().lower()
        overlap = len(a_words & b_words) / min(len(a_words), len(b_words))
        return overlap >= 0.5

    def _chief_synthesize(self, opinions: dict[str, str], votes: list[MentorVote],
                          vote_analysis: dict, critical_opinion: str) -> str:
        """大导师综合裁决：始终包含批判性审查"""
        if not self.chief_mentor:
            return self._fallback_consensus(opinions, votes, vote_analysis)

        # 构建所有导师意见摘要
        opinions_text = "\n".join(f"[{name}]: {op[:500]}" for name, op in opinions.items())

        # 投票摘要
        votes_text = ""
        if votes:
            votes_text = "\n\n投票汇总：\n" + "\n".join(
                f"  - {v.mentor_name}: 推荐={v.method_choice}, "
                f"置信度={v.confidence:.1f}, 行动={v.action}"
                for v in votes
            )

        # 投票预分析
        va = vote_analysis
        analysis_text = f"\n\n投票预分析：{va['summary']}（平均置信度 {va.get('avg_confidence', 0):.1f}）"

        # 批判性审查（始终单独呈现）
        critical_text = ""
        if critical_opinion:
            critical_text = f"\n\n【批判性审查（必须阅读）】：\n{critical_opinion[:800]}"

        status = va.get("status", "unknown")

        # 根据分歧程度调整 prompt 复杂度
        if status == "unanimous":
            prompt = f"""组会讨论结束，各位导师意见如下：

{opinions_text}{votes_text}{analysis_text}{critical_text}

导师们已达成一致。请基于以上意见（特别是批判性审查），给出最终决策：

1. 确认共识：用1-2句话总结最终方案
2. 批判性补充：批判性审查中有没有被忽略的风险？（如果有，补充进去）
3. 具体行动：学生可以直接执行的代码修改

简洁回答。"""
        else:
            prompt = f"""组会讨论结束，各位导师意见如下：

{opinions_text}{votes_text}{analysis_text}{critical_text}

实验历史：
{self.history.get_summary()}

请**逐步推理**后做出最终裁决：

**Step 1: 分析各方意见** - 各导师核心观点？一致和分歧在哪？
**Step 2: 评估批判性审查** - 批判性导师指出了哪些被忽略的风险？这些风险是否成立？
**Step 3: 权衡证据** - 哪些建议有数据支持？哪些只是推测？
**Step 4: 做出裁决** - 综合考虑所有意见和批判性审查，给出最终方案
**Step 5: 具体行动** - 可执行的代码修改（具体到参数值）

要求：
- 批判性审查中的风险必须被回应（接受或反驳）
- 选择有数据支持的方案
- 具体到可以执行的代码级别"""

        try:
            return self.chief_mentor._ask(prompt, max_tokens=64000, deep_think=True)
        except Exception:
            return self._fallback_consensus(opinions, votes, vote_analysis)

    def _fallback_consensus(self, opinions: dict[str, str], votes: list[MentorVote],
                            vote_analysis: dict) -> str:
        """大导师不可用时的降级方案"""
        status = vote_analysis.get("status", "unknown")

        if status == "unanimous" and votes:
            best = max(votes, key=lambda v: v.confidence)
            return f"共识：{best.method_choice} | 行动：{best.action}"

        if status == "majority" and votes:
            best = max(votes, key=lambda v: v.confidence)
            return f"多数共识：{best.method_choice} | 行动：{best.action}"

        # 严重分歧且无大导师，取最高置信度
        if votes:
            best = max(votes, key=lambda v: v.confidence)
            return f"降级裁决（无大导师）：{best.method_choice} | 行动：{best.action}"

        return "未能达成共识"

    # ==================== 行动项 ====================

    def _determine_actions(self, minutes: MeetingMinutes) -> list[str]:
        """优先从大导师共识中提取，降级到投票"""
        if not minutes.consensus:
            return []

        # 如果共识已经包含具体行动，直接提取
        if self.chief_mentor:
            return self._extract_actions_from_consensus(minutes.consensus)

        # 降级：从投票中取
        valid_votes = [v for v in minutes.mentor_votes if v.action]
        if valid_votes:
            best = max(valid_votes, key=lambda v: v.confidence)
            return [best.action]

        return []

    def _extract_actions_from_consensus(self, consensus: str) -> list[str]:
        """从大导师的共识文本中提取行动项"""
        if not self.chief_mentor:
            return []

        prompt = f"""从以下决策中提取具体的行动项（每项一句话，学生可以直接执行）：

{consensus}

格式：
1. 行动项1
2. 行动项2
3. 行动项3"""

        try:
            result = self.chief_mentor._ask(prompt, max_tokens=300)
            return [line.strip() for line in result.split("\n")
                    if line.strip() and line.strip()[0].isdigit()]
        except Exception:
            return []
