"""讯飞星辰 — OpenAI 兼容协议，默认启用深度思考"""
import httpx
import openai
from api.base import BaseProvider
from api.credentials import get_credential


class XfyunProvider(BaseProvider):
    name = "xfyun"
    prefixes = ["xfyun/"]

    def create_client(self, model: str, **kwargs) -> tuple:
        actual = self.strip_prefix(model)
        cred = get_credential("xfyun")
        ipv4_transport = httpx.HTTPTransport(local_address="0.0.0.0")
        http_client = httpx.Client(transport=ipv4_transport)
        client = openai.OpenAI(
            api_key=cred["api_key"],
            base_url=cred.get("base_url", "https://maas-coding-api.cn-huabei-1.xf-yun.com/v2"),
            http_client=http_client,
            **kwargs,
        )
        print(f"[api] xfyun client ready, model={actual}")
        return client, actual, model

    def get_call_params(self, model: str) -> dict:
        return {"extra_body": {"enable_thinking": True}}

    def _check(self) -> bool:
        cred = get_credential("xfyun")
        return bool(cred.get("api_key"))
