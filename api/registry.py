"""Provider 注册表"""
from typing import Any, Optional
from api.providers import ALL_PROVIDERS
from api.base import BaseProvider


class ProviderRegistry:
    def __init__(self):
        self._providers: list[BaseProvider] = list(ALL_PROVIDERS)
        self._available: list[BaseProvider] = []
        self._checked = False

    def check_all(self) -> dict[str, bool]:
        results = {}
        for p in self._providers:
            ok = p.is_available()
            results[p.name] = ok
            if ok and p not in self._available:
                self._available.append(p)
        self._checked = True
        return results

    def get_provider(self, model: str) -> Optional[BaseProvider]:
        for p in self._providers:
            if p.handles(model):
                return p
        return None

    def create_client(self, model: str, **kwargs) -> tuple[Any, str, str]:
        provider = self.get_provider(model)
        if provider is None:
            raise ValueError(f"Model '{model}' not handled by any provider. "
                             f"Available: {[p.name for p in self._providers]}")
        return provider.create_client(model, **kwargs)

    def call_completion(self, model: str, client: Any, actual_model: str,
                        messages: list, temperature: float, max_tokens: int,
                        n: int = 1, seed: int = 0, **kwargs) -> Any:
        provider = self.get_provider(model)
        if provider is None:
            raise ValueError(f"No provider for model: {model}")
        response = provider.call_completion(
            client, actual_model, messages, temperature, max_tokens,
            n=n, seed=seed, **kwargs
        )
        self._track_usage(model, response)
        return response

    @staticmethod
    def _track_usage(model: str, response: Any):
        try:
            usage = getattr(response, "usage", None)
            if usage is None:
                return
            from lib.token_tracker import token_tracker
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            reasoning_tokens = 0
            cached_tokens = 0
            details = getattr(usage, "completion_tokens_details", None)
            if details is not None:
                reasoning_tokens = getattr(details, "reasoning_tokens", 0) or 0
            prompt_details = getattr(usage, "prompt_tokens_details", None)
            if prompt_details is not None:
                cached_tokens = getattr(prompt_details, "cached_tokens", 0) or 0
            token_tracker.add_tokens(
                model, prompt_tokens, completion_tokens,
                reasoning_tokens, cached_tokens,
            )
        except Exception:
            pass

    def extract_content(self, model: str, response) -> list[str]:
        provider = self.get_provider(model)
        if provider is None:
            try:
                return [response.choices[0].message.content]
            except (IndexError, AttributeError):
                return [str(response)]
        return provider.extract_content(response)

    def is_anthropic(self, model: str) -> bool:
        provider = self.get_provider(model)
        return provider.is_anthropic if provider else False

    def list_available(self) -> list[str]:
        if not self._checked:
            self.check_all()
        return [p.name for p in self._available]

    def list_all(self) -> list[dict]:
        if not self._checked:
            self.check_all()
        return [
            {"name": p.name, "prefixes": p.prefixes, "available": p.is_available()}
            for p in self._providers
        ]


_registry: Optional[ProviderRegistry] = None


def get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry
