"""Provider 基类：封装 client 创建 + API 调用"""
from abc import ABC, abstractmethod
from typing import Any


class BaseProvider(ABC):
    name: str
    prefixes: list[str]
    _available: bool | None = None

    @abstractmethod
    def create_client(self, model: str, **kwargs) -> tuple:
        """返回 (client, stripped_model, original_model)
           stripped_model: 去掉前缀的实际模型名，用于 API 调用
           original_model: 原始输入，用于 provider 路由
        """
        ...

    def call_completion(self, client, model: str, messages: list, temperature: float,
                        max_tokens: int, n: int = 1, seed: int = 0, **kwargs) -> Any:
        extra = self.get_call_params(model)
        params = {"model": model, "messages": messages, "temperature": temperature,
                  "max_tokens": max_tokens, "n": n}
        if seed:
            params["seed"] = seed
        params.update(extra)
        params.update(kwargs)
        params = {k: v for k, v in params.items() if v is not None}
        return client.chat.completions.create(**params)

    def extract_content(self, response) -> list[str]:
        results = []
        for c in response.choices:
            msg = c.message
            content = getattr(msg, 'content', None) or ''
            reasoning = getattr(msg, 'reasoning_content', None) or ''
            if not content and reasoning:
                content = reasoning
            elif content and reasoning:
                content = reasoning + "\n" + content
            results.append(content)
        return results

    def get_call_params(self, model: str) -> dict:
        return {}

    def strip_prefix(self, model: str) -> str:
        for prefix in self.prefixes:
            if model.startswith(prefix):
                return model[len(prefix):]
        return model

    def handles(self, model: str) -> bool:
        return any(model.startswith(p) for p in self.prefixes)

    @property
    def is_anthropic(self) -> bool:
        return False

    def is_available(self) -> bool:
        if self._available is None:
            self._available = self._check()
        return self._available

    @abstractmethod
    def _check(self) -> bool:
        ...
