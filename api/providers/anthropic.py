"""Anthropic 系列 — claude-*, bedrock, vertex_ai, c_anth/
   覆盖 call_completion / extract_content 以适配 Anthropic 协议"""
import os
from typing import Any
import anthropic
from api.base import BaseProvider
from api.credentials import get_credential


class AnthropicProvider(BaseProvider):
    name = "anthropic"
    prefixes = ["claude-", "bedrock/", "vertex_ai/", "c_anth/"]

    @property
    def is_anthropic(self) -> bool:
        return True

    def create_client(self, model: str, **kwargs) -> tuple:
        if model.startswith("bedrock") and "claude" in model:
            client = anthropic.AnthropicBedrock(**kwargs)
            actual = model.split("/")[-1]
        elif model.startswith("vertex_ai") and "claude" in model:
            client = anthropic.AnthropicVertex(**kwargs)
            actual = model.split("/")[-1]
        elif model.startswith("c_anth/"):
            cred = get_credential("anthropic")
            client = anthropic.Anthropic(
                api_key=cred.get("api_key", ""),
                base_url=cred.get("base_url", ""),
                **kwargs,
            )
            actual = self.strip_prefix(model)
        else:
            client = anthropic.Anthropic(**kwargs)
            actual = model
        print(f"[api] anthropic client ready, model={actual}")
        return client, actual, model

    def call_completion(self, client, model: str, messages: list, temperature: float,
                        max_tokens: int, n: int = 1, seed: int = 0, **kwargs) -> Any:
        if n > 1:
            import logging
            logging.warning("[anthropic] n=%d requested but Anthropic API does not support multiple completions", n)
        system_msg = ""
        for m in messages:
            if m.get("role") == "system":
                system_msg = m.get("content", "")
                break
        user_messages = [m for m in messages if m.get("role") != "system"]
        return client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_msg,
            messages=user_messages,
        )

    def extract_content(self, response) -> list[str]:
        if hasattr(response, 'content') and len(response.content) == 2 and response.content[0].type == "thinking":
            return [response.content[1].text]
        return [response.content[0].text]

    def _check(self) -> bool:
        cred = get_credential("anthropic")
        return bool(cred.get("api_key")) or bool(os.environ.get("ANTHROPIC_API_KEY"))
