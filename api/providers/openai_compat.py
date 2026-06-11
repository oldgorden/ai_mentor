"""OpenAI 兼容 provider — custom/, custom2/, deepseek, gemini, kimi_oai/ 等"""
import httpx
import openai
from api.base import BaseProvider
from api.credentials import get_credential


class OpenAICompatProvider(BaseProvider):
    name = "openai_compat"
    prefixes = ["custom/", "custom2/", "kimi_oai/", "deepseek-coder", "deepcoder",
                "llama3.1-405b", "gemini", "opencode-go/"]

    _ROUTES = {
        "custom/":     ("custom",     None,             True),
        "custom2/":    ("custom2",    None,             True),
        "kimi_oai/":   ("anthropic",  "https://api.kimi.com/coding/v1", True),
        "deepseek-coder-v2-0724": ("deepseek",    "https://api.deepseek.com", False),
        "deepcoder-14b": ("huggingface", "https://api-inference.huggingface.co/models/agentica-org/DeepCoder-14B-Preview", False),
        "llama3.1-405b": ("openrouter",  "https://openrouter.ai/api/v1", False),
        "gemini":       ("gemini",      "https://generativelanguage.googleapis.com/v1beta/openai/", False),
        "opencode-go/": ("opencode-go", "https://opencode.ai/zen/go/v1", True),
    }

    def _match_route(self, model: str):
        for prefix, route in self._ROUTES.items():
            if model.startswith(prefix) or model == prefix:
                return route
        return None

    def create_client(self, model: str, **kwargs) -> tuple:
        route = self._match_route(model)
        if route is None:
            raise ValueError(f"No route for model: {model}")

        cred_name, default_url, strip = route
        cred = get_credential(cred_name)
        actual_model = self.strip_prefix(model) if strip else model
        if model == "llama3.1-405b":
            actual_model = "meta-llama/llama-3.1-405b-instruct"
        if model == "deepseek-coder-v2-0724":
            actual_model = "deepseek-coder"

        api_key = cred.get("api_key", "")
        base_url = cred.get("base_url") or default_url or ""

        client_kwargs = {"api_key": api_key, "base_url": base_url}
        client_kwargs.update(kwargs)

        if model.startswith("kimi_oai/"):
            client_kwargs["http_client"] = httpx.Client(
                headers={"User-Agent": "KimiCLI/1.0"}, timeout=kwargs.get("timeout", None)
            )
        elif api_key and base_url:
            ipv4_transport = httpx.HTTPTransport(local_address="0.0.0.0")
            client_kwargs.setdefault("http_client", httpx.Client(transport=ipv4_transport))

        client = openai.OpenAI(**client_kwargs)
        print(f"[api] {cred_name} client ready, model={actual_model}")
        return client, actual_model, model

    def handles(self, model: str) -> bool:
        return self._match_route(model) is not None

    def _check(self) -> bool:
        for _, (cred_name, *_) in self._ROUTES.items():
            cred = get_credential(cred_name)
            if cred.get("api_key"):
                return True
        return False
