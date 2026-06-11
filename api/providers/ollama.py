"""Ollama 本地模型"""
import os
import openai
from api.base import BaseProvider


class OllamaProvider(BaseProvider):
    name = "ollama"
    prefixes = ["ollama/"]

    def create_client(self, model: str, **kwargs) -> tuple:
        actual = self.strip_prefix(model)
        client = openai.OpenAI(
            api_key=os.environ.get("OLLAMA_API_KEY", ""),
            base_url="http://localhost:11434/v1",
            **kwargs,
        )
        print(f"[api] ollama client ready, model={actual}")
        return client, actual, model

    def _check(self) -> bool:
        return True
