"""原生 OpenAI — gpt-*, o1*, o3*"""
import openai
from api.base import BaseProvider


class NativeOpenAIProvider(BaseProvider):
    name = "openai"
    prefixes = []

    def create_client(self, model: str, **kwargs) -> tuple:
        client = openai.OpenAI(**kwargs)
        print(f"[api] native OpenAI client ready, model={model}")
        return client, model, model

    def handles(self, model: str) -> bool:
        return "gpt" in model or model.startswith("o1") or model.startswith("o3")

    def _check(self) -> bool:
        import os
        return bool(os.environ.get("OPENAI_API_KEY"))
