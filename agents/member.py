"""
Member — AI 决策者（导师 + 研究生统一）

Group 中的成员，拥有角色、权限、置信度和 LLM 模型。
收到事件后用 LLM 分析上下文，从可用工具中选择合适的并返回 Action。

成员类型:
    member_type="mentor"        导师（出脑力：指导、审稿、决策）
    member_type="postgrad"      研究生（干脏活：实验、写论文、搜文献）

关键方法:
    decide(event, tools, context) → Action    LLM 决策
    has_permission(perm) → bool               fnmatch 权限检查

上下文管理:
    每个成员独立维护对话历史 (_msg_history)，
    system prompt 固定在首位，后续 user/assistant 交替追加。
    当历史过长时自动截断旧消息，保留最近 N 轮。
"""
import asyncio
import json
import fnmatch
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path

from agents.event import Event
from agents.tool import Tool, ToolResult
from agents.context import SharedContext

MAX_HISTORY_ROUNDS = 20
MAX_HISTORY_CHARS = 60000
MAX_TOOL_RESULT_CHARS = 1500
METHODOLOGY_REMINDER_INTERVAL = 5

_METHODOLOGY_REMINDER = (
    "\n\n⚠️ 方法学提醒：遵守科学严谨性——基线对比、控制变量、统计显著性、消融实验、如实报告。"
    "不要编造数据。不要过度声称。"
)


@dataclass
class Action:
    tool_name: str
    params: dict = field(default_factory=dict)
    thought: str = ""
    confidence: float = 0.5


class Member:
    def __init__(self, name: str, role: str, permissions: list[str],
                 confidence: float, model: str, member_type: str = "mentor",
                 prompt_file: str = "", root: Path = None,
                 max_tokens: int = 128000, temperature: float = 0.7):
        self.name = name
        self.role = role
        self.member_type = member_type
        self.permissions = permissions
        self.confidence = confidence
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.root = root or Path.cwd()

        self._client = None
        self._actual_model = None
        self._original_model = None
        self._system_prompt = ""
        self._msg_history: list[dict] = []
        self._context: Optional[SharedContext] = None

        prompts_dir = Path(__file__).parent / "prompts"
        permanent_dir = prompts_dir / "permanent"
        temporary_dir = prompts_dir / "temporary"

        if prompt_file:
            if member_type == "postgrad":
                p = Path(__file__).parent.parent / "postgraduates" / "prompts" / prompt_file
            else:
                p = permanent_dir / prompt_file
            if p.exists():
                self._system_prompt = p.read_text()
            else:
                self._system_prompt = f"你是{role}。"

        tools_ref_path = permanent_dir / "tools_reference.md"
        if tools_ref_path.exists():
            content = tools_ref_path.read_text().strip()
            if content:
                self._system_prompt += "\n\n---\n\n# 工具参考手册\n\n" + content

        if temporary_dir.is_dir():
            role_key = name.split("_")[0] if name else ""
            for md in sorted(temporary_dir.glob("*.md")):
                stem = md.stem
                dot_pos = stem.find(".")
                if dot_pos > 0:
                    prefixes = [p.strip() for p in stem[:dot_pos].split(",")]
                    if role_key not in prefixes:
                        continue
                    display_name = stem[dot_pos + 1:]
                else:
                    display_name = stem
                content = md.read_text().strip()
                if content:
                    header = display_name.replace("_", " ").title()
                    self._system_prompt += f"\n\n---\n\n# {header}\n\n" + content

    def init_client(self):
        from api import create_client
        self._client, self._actual_model, self._original_model = create_client(self.model)

    def has_permission(self, required: str) -> bool:
        for pattern in self.permissions:
            if fnmatch.fnmatch(required, pattern):
                return True
        return False

    def get_available_tools(self, tools: dict[str, Tool]) -> list[Tool]:
        return [t for t in tools.values() if self.has_permission(t.permission)]

    def _trim_history(self):
        while len(self._msg_history) > MAX_HISTORY_ROUNDS * 2:
            self._msg_history.pop(0)

        while self._history_chars() > MAX_HISTORY_CHARS and len(self._msg_history) > 4:
            self._msg_history.pop(0)

    def _history_chars(self) -> int:
        return sum(len(m.get("content", "")) for m in self._msg_history)

    DECIDE_TIMEOUT = 180

    async def decide(self, context: SharedContext, event: Event,
                     tools: dict[str, Tool]) -> Optional[Action]:
        if self._client is None:
            try:
                self.init_client()
            except Exception as e:
                print(f"[error] {self.name} init_client failed: {e}")
                return None

        available = self.get_available_tools(tools)
        if not available:
            return None

        tool_schemas = json.dumps([t.get_schema() for t in available], ensure_ascii=False, indent=2)

        pending_msgs = self.get_messages()
        msg_section = ""
        if pending_msgs:
            msg_section = f"\n\n## 来自其他成员的消息\n{json.dumps(pending_msgs, ensure_ascii=False, indent=2)}\n"

        user_msg = (
            f"## 当前状态\n{context.summary_for_prompt(member=self.name)}\n\n"
            f"## 收到事件\n类型: {event.type}\n数据: {json.dumps(event.data, ensure_ascii=False)}\n\n"
            f"## 可用工具\n{tool_schemas}\n"
            f"{msg_section}"
            f"\n请分析情况并决定下一步操作。直接返回 JSON，不要解释：\n"
            f'{{"thought": "简短分析(50字以内)", "tool": "工具名", "params": {{...}}, "confidence": 0.0-1.0}}\n'
            f"如果不需要操作，返回: {{\"thought\": \"...\", \"tool\": null}}"
        )

        n_history = len(self._msg_history) // 2
        if n_history > 0 and n_history % METHODOLOGY_REMINDER_INTERVAL == 0:
            user_msg += _METHODOLOGY_REMINDER

        from api import get_registry
        provider = get_registry().get_provider(self._original_model)
        if provider is None:
            return None

        messages = [
            {"role": "system", "content": self._system_prompt},
            *self._msg_history,
            {"role": "user", "content": user_msg},
        ]

        try:
            import time as _t
            _t0 = _t.time()
            print(f"[api-call] {self.name} calling {self._actual_model}...", flush=True)
            response = provider.call_completion(
                self._client, self._actual_model, messages,
                self.temperature, self.max_tokens,
            )
            _elapsed = _t.time() - _t0
            print(f"[api-call] {self.name} {self._actual_model} done in {_elapsed:.1f}s", flush=True)
        except Exception as e:
            print(f"[error] {self.name} decide() API error: {e}", flush=True)
            import traceback
            traceback.print_exc()
            sys.stdout.flush()
            return None

        try:
            contents = provider.extract_content(response)
        except Exception as e:
            print(f"[error] {self.name} extract_content failed: {e}", flush=True)
            return None
        if not contents or not contents[0].strip():
            print(f"[warn] {self.name} empty response content", flush=True)
            return None

        assistant_text = contents[0]
        print(f"[decide] {self.name} parsing action from {len(assistant_text)} chars...", flush=True)
        action = self._parse_action(assistant_text)
        print(f"[decide] {self.name} parsed: tool={action.tool_name if action else None}", flush=True)
        if action is None and "tool" not in assistant_text:
            print(f"[warn] {self.name} no JSON in response, prompting retry", flush=True)
            self._msg_history.append({"role": "user", "content": user_msg})
            self._msg_history.append({"role": "assistant", "content": assistant_text})
            retry_msg = (
                "上一次回复没有包含有效的 JSON。"
                "你必须直接回复如下格式的 JSON，不要有任何其他文字：\n"
                '{"thought":"简短分析","tool":"工具名或null","params":{},"confidence":0.8}'
            )
            try:
                response = provider.call_completion(
                    self._client, self._actual_model,
                    [
                        {"role": "system", "content": self._system_prompt},
                        *self._msg_history,
                        {"role": "user", "content": retry_msg},
                    ],
                    self.temperature, self.max_tokens,
                )
                retry_contents = provider.extract_content(response)
                if retry_contents and retry_contents[0].strip():
                    assistant_text = retry_contents[0]
                    self._msg_history.append({"role": "user", "content": retry_msg})
                    self._msg_history.append({"role": "assistant", "content": assistant_text})
                    self._trim_history()
                    return self._parse_action(assistant_text)
            except Exception as e:
                print(f"[warn] {self.name} retry failed: {e}")
            self._trim_history()
            return None

        assistant_text = contents[0]
        self._msg_history.append({"role": "user", "content": user_msg})
        self._msg_history.append({"role": "assistant", "content": assistant_text})
        self._trim_history()

        return self._parse_action(assistant_text)

    def inject_tool_result(self, tool_name: str, params: dict,
                           result: ToolResult):
        result_data = result.data if result.success else None
        if result_data and isinstance(result_data, dict):
            result_data = self._summarize_tool_data(tool_name, result_data)

        result_text = json.dumps({
            "tool": tool_name,
            "params": params,
            "success": result.success,
            "data": result_data,
            "error": result.error if not result.success else None,
        }, ensure_ascii=False, indent=2)
        self._msg_history.append({
            "role": "user",
            "content": f"## 工具执行结果\n{result_text}",
        })
        self._trim_history()

    def _summarize_tool_data(self, tool_name: str, data: dict) -> dict:
        total_chars = len(json.dumps(data, ensure_ascii=False))
        if total_chars <= MAX_TOOL_RESULT_CHARS:
            return data

        out = {}
        for key, value in data.items():
            if isinstance(value, str) and len(value) > MAX_TOOL_RESULT_CHARS:
                out[key] = value[:MAX_TOOL_RESULT_CHARS] + f"\n... [truncated, {len(value)} total chars]"
            elif isinstance(value, list) and len(json.dumps(value, ensure_ascii=False)) > MAX_TOOL_RESULT_CHARS:
                out[key] = f"[{len(value)} items, showing first 5] " + json.dumps(value[:5], ensure_ascii=False)
            elif isinstance(value, dict) and len(json.dumps(value, ensure_ascii=False)) > MAX_TOOL_RESULT_CHARS:
                out[key] = f"[dict with {len(value)} keys] " + json.dumps(
                    {k: str(v)[:100] for k, v in list(value.items())[:10]}, ensure_ascii=False
                )
            else:
                out[key] = value
        return out

    def send_message(self, to_member: str, content: dict):
        if self._context:
            self._context.send_message(self.name, to_member, content)

    def get_messages(self) -> list[dict]:
        if self._context:
            msgs = self._context.get_messages(self.name)
            return [asdict(m) for m in msgs] if msgs else []
        return []

    def set_context(self, context: SharedContext):
        self._context = context

    def _parse_action(self, text: str) -> Optional[Action]:
        try:
            for block in text.split("```"):
                block = block.strip()
                if block.startswith("json"):
                    block = block[4:]
                if "{" in block and "tool" in block:
                    start = block.index("{")
                    end = block.rfind("}") + 1
                    if end == 0:
                        end = len(block)
                    candidate = block[start:end]
                    try:
                        data = json.loads(candidate)
                    except json.JSONDecodeError:
                        for fix_end in range(end - 1, start, -1):
                            if block[fix_end] == '}':
                                try:
                                    data = json.loads(block[start:fix_end + 1])
                                    break
                                except json.JSONDecodeError:
                                    continue
                        else:
                            data = self._try_fix_truncated_json(block[start:])
                            if data is None:
                                continue
                    if data.get("tool") is None:
                        return None
                    return Action(
                        tool_name=data["tool"],
                        params=data.get("params", {}),
                        thought=data.get("thought", ""),
                        confidence=min(data.get("confidence", self.confidence), 1.0),
                    )
        except (json.JSONDecodeError, ValueError):
            pass
        return None

    @staticmethod
    def _try_fix_truncated_json(text: str) -> dict | None:
        import re
        tool_match = re.search(r'"tool"\s*:\s*"([^"]+)"', text)
        if not tool_match:
            return None
        thought_match = re.search(r'"thought"\s*:\s*"([^"]*)', text)
        params_match = re.search(r'"params"\s*:\s*(\{[^}]{0,500})', text)
        conf_match = re.search(r'"confidence"\s*:\s*([\d.]+)', text)
        result = {
            "tool": tool_match.group(1),
            "thought": thought_match.group(1) if thought_match else "",
            "params": {},
            "confidence": 0.7,
        }
        if params_match:
            try:
                result["params"] = json.loads(params_match.group(1) + "}")
            except Exception:
                pass
        if conf_match:
            result["confidence"] = float(conf_match.group(1))
        return result
