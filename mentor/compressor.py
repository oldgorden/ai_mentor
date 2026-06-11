"""上下文压缩器：用 LLM 做语义压缩，规则兜底"""


class ContextCompressor:
    """上下文压缩器（LLM 语义压缩 + 规则兜底）"""

    def __init__(self, llm=None, logger=None):
        self.llm = llm
        self.logger = logger

    # ==================== 导师上下文压缩 ====================

    def compress_mentor_context(self, history, temp_kb, target_tokens: int = 50000) -> str:
        """压缩导师的临时上下文：LLM 提炼结构化总结，失败则规则兜底"""
        if self.logger:
            self.logger.log("[压缩器] 压缩导师上下文")

        # 收集原始素材
        raw = self._collect_mentor_raw(history, temp_kb)
        if not raw:
            if self.logger:
                self.logger.log("[压缩器] 导师上下文为空，跳过")
            return ""

        # 尝试 LLM 压缩
        if self.llm:
            compressed = self._llm_compress_mentor(raw, target_tokens)
            if compressed:
                if self.logger:
                    self.logger.log(f"[压缩器] LLM 导师压缩完成: {len(compressed)}字")
                return compressed
            if self.logger:
                self.logger.log("[压缩器] LLM 导师压缩失败，降级到规则", "WARN")

        # 规则兜底
        return self._rule_compress_mentor(raw)

    def _collect_mentor_raw(self, history, temp_kb) -> dict:
        """收集导师上下文原始数据"""
        raw = {}

        # 失败记录（最近 10 条，含分析）
        failures = temp_kb.query(category="failure", limit=10)
        if failures:
            raw["failures"] = [
                {"summary": f.summary, "content": f.content[:300]}
                for f in failures
            ]

        # 成功记录（最近 5 条）
        successes = temp_kb.query(category="success", limit=5)
        if successes:
            raw["successes"] = [
                {"summary": s.summary, "metric": s.content[:200]}
                for s in successes
            ]

        # 共识
        consensus = temp_kb.query(category="consensus", limit=2)
        if consensus:
            raw["consensus"] = "\n".join(c.content[:300] for c in consensus)

        # 失败模式
        if history.failure_patterns:
            raw["failure_patterns"] = [
                {"pattern": p.pattern, "count": p.count, "nodes": p.examples}
                for p in history.failure_patterns
            ]

        # 实验记录（最近 15 条，带指标）
        recent = history.records[-15:]
        if recent:
            raw["recent_records"] = [
                {
                    "node": r.node_id,
                    "buggy": r.is_buggy,
                    "analysis": r.analysis[:200] if r.analysis else "",
                    "metric": str(r.metric)[:100] if r.metric else "",
                }
                for r in recent
            ]

        # 压缩知识库
        compressed_kb = temp_kb.compress(keep_recent=20)
        if compressed_kb:
            raw["kb_archive"] = compressed_kb[:1500]

        return raw

    def _llm_compress_mentor(self, raw: dict, target_tokens: int) -> str:
        """LLM 压缩导师上下文"""
        import json
        raw_text = json.dumps(raw, ensure_ascii=False, indent=2)
        if len(raw_text) > 30000:
            raw_text = raw_text[:30000] + "\n... (截断)"

        prompt = f"""你是实验总结专家。以下是导师团队在指导学生实验过程中积累的原始数据（失败记录、成功记录、共识、失败模式、最近实验节点、知识库归档）。

请将这些原始数据压缩为一份结构化的实验总结报告。要求：

1. **关键发现**：实验中发现了什么规律？（从成功和失败中提炼）
2. **有效方法**：哪些方法/参数被验证有效？（具体到参数值）
3. **失败规律**：重复出现的失败模式是什么？根本原因？
4. **导师共识**：团队达成的共识和指导方针
5. **禁忌清单**：哪些做法被验证无效？（避免重复犯错）

输出要求：
- 中文，简洁，信息密度高
- 保留所有具体的参数值和指标数值
- 原始数据中的重复信息只保留一次
- 总长度控制在 2000 字以内

原始数据：
{raw_text}"""

        try:
            return self.llm._ask(prompt, max_tokens=8000, deep_think=False)
        except Exception as e:
            if self.logger:
                self.logger.log(f"[压缩器] LLM 导师压缩异常: {e}", "WARN")
            return ""

    def _rule_compress_mentor(self, raw: dict) -> str:
        """规则兜底：拼字符串"""
        parts = []

        if "failures" in raw:
            parts.append("最近失败记录:")
            for f in raw["failures"][:5]:
                parts.append(f"  - {f['summary']}")

        if "successes" in raw:
            parts.append("最近成功记录:")
            for s in raw["successes"][:3]:
                parts.append(f"  - {s['summary']}")

        if "consensus" in raw:
            parts.append(f"导师共识: {raw['consensus'][:300]}")

        if "failure_patterns" in raw:
            parts.append("失败模式:")
            for p in raw["failure_patterns"]:
                parts.append(f"  - {p['pattern']}: {p['count']}次")

        if "kb_archive" in raw:
            parts.append(f"历史摘要:\n{raw['kb_archive'][:1000]}")

        result = "\n".join(parts)
        if self.logger:
            self.logger.log(f"[压缩器] 规则兜底导师压缩完成: {len(result)}字")
        return result

    # ==================== 学生上下文压缩 ====================

    def compress_student_context(self, nodes: list, target_tokens: int = 30000) -> str:
        """压缩学生的临时上下文：LLM 语义总结 + 保留最佳代码，失败则规则兜底"""
        if self.logger:
            self.logger.log(f"[压缩器] 压缩学生上下文 ({len(nodes)} 节点)")

        if not nodes:
            return ""

        # 提取原始素材
        raw = self._collect_student_raw(nodes)

        # 保留最佳代码（不截断）
        best_code = self._extract_best_code(nodes)

        # 尝试 LLM 压缩
        if self.llm:
            summary = self._llm_compress_student(raw, target_tokens)
            if summary:
                result = f"=== 实验语义总结 ===\n{summary}\n\n=== 最佳代码（完整保留） ===\n{best_code}"
                if self.logger:
                    self.logger.log(f"[压缩器] LLM 学生压缩完成: {len(result)}字")
                return result
            if self.logger:
                self.logger.log("[压缩器] LLM 学生压缩失败，降级到规则", "WARN")

        # 规则兜底
        return self._rule_compress_student(nodes, raw, best_code)

    def _collect_student_raw(self, nodes: list) -> dict:
        """收集学生实验原始数据（不含完整代码，避免 token 浪费）"""
        good_nodes = [n for n in nodes if not n.get("is_buggy")]
        buggy_nodes = [n for n in nodes if n.get("is_buggy")]

        raw = {
            "total": len(nodes),
            "good_count": len(good_nodes),
            "buggy_count": len(buggy_nodes),
        }

        # 成功节点：只保留指标和分析（代码在 best_code 里完整保留）
        if good_nodes:
            raw["successes"] = []
            for n in good_nodes:
                raw["successes"].append({
                    "metric": str(n.get("metric", ""))[:150],
                    "analysis": n.get("analysis", "")[:200],
                    "plan": n.get("plan", "")[:100],
                })

        # 失败节点：分析 + 错误类型
        if buggy_nodes:
            raw["failures"] = []
            for n in buggy_nodes[-15:]:
                raw["failures"].append({
                    "analysis": n.get("analysis", "")[:200],
                    "plan": n.get("plan", "")[:100],
                })

        return raw

    def _extract_best_code(self, nodes: list) -> str:
        """提取最佳节点的完整代码（不截断）"""
        good_nodes = [n for n in nodes if not n.get("is_buggy") and n.get("code")]

        if not good_nodes:
            # 没有成功节点，取最后一个有代码的节点
            for n in reversed(nodes):
                if n.get("code"):
                    return n["code"]
            return ""

        # 取指标最好的
        def metric_val(n):
            m = n.get("metric", {})
            if isinstance(m, dict):
                v = m.get("value", 0)
                if isinstance(v, (int, float)):
                    return float(v)
            return 0.0

        best = max(good_nodes, key=metric_val)
        metric_str = str(best.get("metric", ""))[:150]
        return f"# 指标: {metric_str}\n{best['code']}"

    def _llm_compress_student(self, raw: dict, target_tokens: int) -> str:
        """LLM 对学生实验做语义总结"""
        import json
        raw_text = json.dumps(raw, ensure_ascii=False, indent=2)
        if len(raw_text) > 30000:
            raw_text = raw_text[:30000] + "\n... (截断)"

        prompt = f"""你是实验分析专家。以下是学生在自动科研过程中产生的实验历史数据。
总共有 {raw['total']} 个实验节点，其中 {raw['good_count']} 个成功，{raw['buggy_count']} 个失败。

请对整个实验历程做一份语义总结。要求：

1. **探索历程**：学生先后尝试了哪些方法？按时间顺序梳理
2. **关键发现**：哪些方法/参数有效？哪些无效？（保留具体参数值和指标数值）
3. **参数趋势**：参数调整的方向和规律（比如 learning rate 从大到小，batch size 从小到大）
4. **失败教训**：主要的失败模式和根本原因
5. **最佳实践**：如果重新做，应该用什么方法和参数起点？

输出要求：
- 中文，简洁，信息密度高
- 保留所有具体的参数值和指标数值（这是最重要的，不能丢失）
- 总长度控制在 2000 字以内

原始数据：
{raw_text}"""

        try:
            return self.llm._ask(prompt, max_tokens=8000, deep_think=False)
        except Exception as e:
            if self.logger:
                self.logger.log(f"[压缩器] LLM 学生压缩异常: {e}", "WARN")
            return ""

    def _rule_compress_student(self, nodes: list, raw: dict, best_code: str) -> str:
        """规则兜底：保留最佳代码 + 最近失败分析"""
        parts = []

        # 统计
        parts.append(f"统计: 总{raw['total']}节点, 好{raw['good_count']}个, bug{raw['buggy_count']}个")

        # 最佳代码
        if best_code:
            parts.append(f"\n最佳代码:\n{best_code}")

        # 失败分析
        if raw.get("failures"):
            parts.append("\n最近失败分析:")
            for f in raw["failures"][-5:]:
                parts.append(f"  - {f.get('analysis', '')[:150]}")

        # 成功指标
        if raw.get("successes"):
            parts.append("\n成功节点指标:")
            for s in raw["successes"]:
                parts.append(f"  - {s['metric']}")

        result = "\n".join(parts)
        if self.logger:
            self.logger.log(f"[压缩器] 规则兜底学生压缩完成: {len(result)}字")
        return result

    # ==================== Prompt 生成 ====================

    def generate_student_prompt_with_context(self, compressed_context: str, idea: dict, guidance: str = "") -> str:
        """生成带压缩上下文的学生 prompt"""
        prompt = f"""你是一个 AI 研究员。以下是你的实验历史摘要：

{compressed_context}

研究想法:
Title: {idea.get('Title', '')}
Hypothesis: {idea.get('Short Hypothesis', '')[:500]}
"""

        if guidance:
            prompt += f"\n导师指导:\n{guidance}\n"

        prompt += "\n请基于以上信息生成实验代码。只输出 Python 代码。"
        return prompt
