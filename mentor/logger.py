"""导师日志系统：记录所有行为，方便排查"""
import json
import time
from pathlib import Path
from datetime import datetime


class MentorLogger:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        log_dir.mkdir(parents=True, exist_ok=True)

        # 终端日志
        self.terminal_log = log_dir / "mentor_terminal.log"
        # 结构化行为日志（JSON）
        self.action_log = log_dir / "mentor_actions.jsonl"
        # 文献搜索日志
        self.literature_log = log_dir / "mentor_literature.jsonl"

        self.actions: list[dict] = []

    def log(self, msg: str, level: str = "INFO"):
        """终端日志"""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level}] {msg}"
        print(line, flush=True)
        with open(self.terminal_log, "a") as f:
            f.write(line + "\n")

    def action(self, mentor_name: str, action_type: str, detail: dict):
        """记录导师的结构化行为"""
        entry = {
            "time": datetime.now().isoformat(),
            "mentor": mentor_name,
            "action": action_type,
            **detail,
        }
        self.actions.append(entry)
        with open(self.action_log, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # 也输出到终端
        summary = self._summarize_action(entry)
        self.log(f"[{mentor_name}] {summary}")

    def literature_search(self, mentor_name: str, query: str, results: list[dict]):
        """记录文献搜索"""
        entry = {
            "time": datetime.now().isoformat(),
            "mentor": mentor_name,
            "query": query,
            "num_results": len(results),
            "papers": [
                {
                    "title": p.get("title", ""),
                    "year": p.get("year", ""),
                    "citations": p.get("citationCount", 0),
                }
                for p in results[:10]
            ],
        }
        with open(self.literature_log, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        self.log(f"[{mentor_name}] 文献搜索: '{query}' → {len(results)} 篇")
        for p in results[:3]:
            self.log(f"  - {p.get('title', '')} ({p.get('year', '?')}, 引用:{p.get('citationCount', 0)})")

    def guidance_given(self, mentor_name: str, agent_name: str, guidance: str):
        """记录给出的指导"""
        self.action(mentor_name, "guidance", {
            "agent": agent_name,
            "guidance": guidance[:500],
        })

    def node_injected(self, mentor_name: str, agent_name: str, plan: str):
        """记录注入的节点"""
        self.action(mentor_name, "inject_node", {
            "agent": agent_name,
            "plan": plan[:200],
        })

    def agent_restarted(self, mentor_name: str, agent_name: str, reason: str):
        """记录重启"""
        self.action(mentor_name, "restart_agent", {
            "agent": agent_name,
            "reason": reason,
        })

    def diagnosis(self, mentor_name: str, agent_name: str, diagnosis: str):
        """记录诊断"""
        self.action(mentor_name, "diagnosis", {
            "agent": agent_name,
            "diagnosis": diagnosis,
        })

    def writeup_triggered(self, mentor_name: str, agent_name: str):
        """记录论文撰写"""
        self.action(mentor_name, "trigger_writeup", {
            "agent": agent_name,
        })

    def _summarize_action(self, entry: dict) -> str:
        """生成行为摘要"""
        action = entry.get("action", "")
        if action == "literature_search":
            return f"搜索文献: '{entry.get('query', '')}' → {entry.get('num_results', 0)} 篇"
        elif action == "guidance":
            return f"指导 {entry.get('agent', '')}: {entry.get('guidance', '')[:80]}..."
        elif action == "inject_node":
            return f"注入节点到 {entry.get('agent', '')}: {entry.get('plan', '')[:60]}"
        elif action == "restart_agent":
            return f"重启 {entry.get('agent', '')}: {entry.get('reason', '')}"
        elif action == "diagnosis":
            return f"诊断 {entry.get('agent', '')}: {entry.get('diagnosis', '')[:80]}"
        elif action == "trigger_writeup":
            return f"触发论文撰写: {entry.get('agent', '')}"
        return f"{action}: {json.dumps(entry, ensure_ascii=False)[:100]}"

    def get_summary(self, last_n: int = 20) -> str:
        """获取最近行为摘要"""
        recent = self.actions[-last_n:]
        lines = [f"最近 {len(recent)} 条导师行为:"]
        for a in recent:
            lines.append(f"  [{a['time'][:19]}] [{a['mentor']}] {self._summarize_action(a)}")
        return "\n".join(lines)
