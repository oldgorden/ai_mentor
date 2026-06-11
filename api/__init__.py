"""
api/ — 统一 API 管理层

所有 LLM/VLM 调用的唯一入口。导师和学生共用。
凭证从 mentor/config.local.json 或环境变量加载。

用法:
    from api import create_client, call_completion, extract_content

    client, actual_model, original_model = create_client("xfyun/astron-code-latest")
    resp = call_completion("xfyun/astron-code-latest", client, actual_model,
                           messages=[...], temperature=0.7, max_tokens=4096)
    content = extract_content("xfyun/astron-code-latest", resp)

文件:
    __init__.py       公共接口：create_client / call_completion / extract_content
    credentials.py    凭证源：config.local.json → config.json → env vars
    base.py           BaseProvider 基类（create_client + call_completion + extract_content）
    registry.py       Provider 注册表：按 model 前缀自动路由

子目录:
    providers/        各厂家 provider 实现
      xfyun.py          讯飞星辰（自动 enable_thinking）
      openai_compat.py  Mimo/DeepSeek/Gemini/Kimi 等 OpenAI 兼容
      native_openai.py  原生 GPT/o1/o3
      anthropic.py      Claude/Bedrock/Vertex/c_anth
      ollama.py         Ollama 本地

凭证配置 (mentor/config.local.json):
    {
      "providers": {
        "custom":  {"api_key": "...", "base_url": "..."},
        "xfyun":   {"api_key": "...", "base_url": "..."}
      }
    }

添加新 Provider:
    1. api/providers/ 下新建文件，继承 BaseProvider
    2. 实现 create_client() 和 _check()
    3. 在 api/providers/__init__.py 的 ALL_PROVIDERS 中注册
    4. 在 config.local.json 的 providers 中加入凭证

Model 前缀路由:
    xfyun/    → XfyunProvider      （enable_thinking=True）
    custom/   → OpenAICompatProvider
    custom2/  → OpenAICompatProvider
    claude-*  → AnthropicProvider
    c_anth/   → AnthropicProvider
    ollama/   → OllamaProvider
    gpt-*     → NativeOpenAIProvider
"""

from api.registry import get_registry


def create_client(model: str, **kwargs):
    """创建 LLM 客户端，返回 (client, actual_model, original_model)"""
    return get_registry().create_client(model, **kwargs)


def call_completion(model: str, client, actual_model: str,
                    messages: list, temperature: float, max_tokens: int,
                    n: int = 1, seed: int = 0, **kwargs):
    """调用 LLM 完成，自动路由到正确的 provider"""
    return get_registry().call_completion(
        model, client, actual_model, messages, temperature, max_tokens,
        n=n, seed=seed, **kwargs
    )


def extract_content(model: str, response) -> list[str]:
    """从响应中提取文本内容"""
    return get_registry().extract_content(model, response)


def is_anthropic(model: str) -> bool:
    """判断是否为 Anthropic 模型"""
    return get_registry().is_anthropic(model)


def list_available() -> list[str]:
    """列出可用的 provider"""
    return get_registry().list_available()

