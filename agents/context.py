"""
SharedContext — Group 内共享状态

所有 Member（导师 + 研究生）通过 SharedContext 共享：
    member_decisions   成员决策历史 {member_name: [MemberDecision]}
    messages           成员间消息队列 {to_member: [MemberMessage]}
    ideas              研究想法列表
    config             组级别配置
"""
import json
import os
import time
import tempfile
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class MemberDecision:
    timestamp: float = 0.0
    member: str = ""
    event_type: str = ""
    event_data: dict = field(default_factory=dict)
    thought: str = ""
    tool_name: str = ""
    tool_params: dict = field(default_factory=dict)
    confidence: float = 0.0
    result_success: bool = False
    result_data: Any = None
    result_error: str = ""


@dataclass
class MemberMessage:
    timestamp: float = 0.0
    from_member: str = ""
    to_member: str = ""
    content: dict = field(default_factory=dict)


@dataclass
class PostgradState:
    name: str = ""
    idea_idx: int = 0
    process_pid: int = 0
    last_progress_time: float = 0.0
    log_path: str = ""
    journal_path: str = ""
    last_node_count: int = 0
    stuck_count: int = 0
    restart_count: int = 0


class SharedContext:
    def __init__(self, root):
        self.root = Path(root)
        self.member_decisions: dict[str, list[MemberDecision]] = {}
        self.messages: dict[str, list[MemberMessage]] = {}
        self.ideas: list[dict] = []
        self.config: dict = {}
        self.active_member: Any = None
        self.postgrads: dict[str, PostgradState] = {}
        self._watcher: Any = None
        self._kb_paths = {
            "permanent": self.root / "mentor" / "permanent_kb.json",
            "temporary": self.root / "mentor" / "temporary_kb.json",
        }
        self._state_file = self.root / "mentor" / "shared_state.json"

    def record_decision(self, member: str, event_type: str, event_data: dict,
                        thought: str, tool_name: str, tool_params: dict,
                        confidence: float, result_success: bool,
                        result_data: Any = None, result_error: str = ""):
        if member not in self.member_decisions:
            self.member_decisions[member] = []
        self.member_decisions[member].append(MemberDecision(
            timestamp=time.time(),
            member=member,
            event_type=event_type,
            event_data=event_data,
            thought=thought,
            tool_name=tool_name,
            tool_params=tool_params,
            confidence=confidence,
            result_success=result_success,
            result_data=result_data,
            result_error=result_error,
        ))

    def get_member_decisions(self, member: str, n: int = 10) -> list[MemberDecision]:
        return self.member_decisions.get(member, [])[-n:]

    def get_all_recent_decisions(self, n: int = 20) -> list[MemberDecision]:
        all_decisions = []
        for decisions in self.member_decisions.values():
            all_decisions.extend(decisions)
        all_decisions.sort(key=lambda d: d.timestamp, reverse=True)
        return all_decisions[:n]

    def send_message(self, from_member: str, to_member: str, content: dict):
        if to_member not in self.messages:
            self.messages[to_member] = []
        self.messages[to_member].append(MemberMessage(
            timestamp=time.time(),
            from_member=from_member,
            to_member=to_member,
            content=content,
        ))

    def get_messages(self, member: str) -> list[MemberMessage]:
        msgs = self.messages.get(member, [])
        self.messages[member] = []
        return msgs

    def load_ideas(self, ideas_path: str):
        p = self.root / ideas_path
        if p.exists():
            with open(p) as f:
                data = json.load(f)
            self.ideas = data if isinstance(data, list) else [data]

    def get_model_config(self) -> dict:
        m = self.active_member
        if m is None:
            return {"model": "custom/mimo-v2.5-pro", "max_tokens": 32768, "temperature": 0.7}
        return {"model": m.model, "max_tokens": m.max_tokens, "temperature": m.temperature}

    def get_watcher(self):
        return self._watcher

    def set_watcher(self, watcher):
        self._watcher = watcher

    def summary_for_prompt(self, member: str = None) -> str:
        parts = [f"想法数量: {len(self.ideas)}"]

        if member:
            decisions = self.get_member_decisions(member, 5)
            if decisions:
                parts.append(f"\n## {member} 最近决策")
                for d in decisions:
                    if d.tool_name:
                        status = "OK" if d.result_success else "FAIL"
                        parts.append(f"  [{d.event_type}] {d.thought[:80]} -> {d.tool_name} ({status})")

        all_decisions = self.get_all_recent_decisions(10)
        other = [d for d in all_decisions if not member or d.member != member]
        if other:
            parts.append("\n## 其他成员最近决策")
            for d in other:
                if d.tool_name:
                    parts.append(f"  [{d.member}] {d.thought[:60]} -> {d.tool_name}")

        return "\n".join(parts)

    def save_state(self, path: Path = None):
        save_path = path or self._state_file
        save_path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "timestamp": time.time(),
            "ideas": self.ideas,
            "member_decisions": {
                member: [asdict(d) for d in decisions[-100:]]
                for member, decisions in self.member_decisions.items()
            },
            "messages": {
                member: [asdict(m) for m in msgs]
                for member, msgs in self.messages.items()
            },
        }
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(save_path.parent), suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, str(save_path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def load_state(self, path: Path = None):
        load_path = path or self._state_file
        if not load_path.exists():
            return False
        try:
            with open(load_path) as f:
                state = json.load(f)
            self.ideas = state.get("ideas", [])
            for member, decisions in state.get("member_decisions", {}).items():
                self.member_decisions[member] = [MemberDecision(**d) for d in decisions]
            for member, msgs in state.get("messages", {}).items():
                self.messages[member] = [MemberMessage(**m) for m in msgs]
            return True
        except Exception as e:
            print(f"[context] load_state failed: {e}")
            return False
