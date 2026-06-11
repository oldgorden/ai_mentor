"""
api/providers/ — 各厂家 LLM Provider 实现

每个 provider 继承 BaseProvider，实现 create_client() 和 _check()。
registry.py 按模型名前缀自动选择 provider。

Provider 列表:
    xfyun.py          讯飞星辰   前缀: xfyun/       特性: 自动 enable_thinking=True
    openai_compat.py  OpenAI兼容 前缀: custom/ custom2/ 支持: Mimo/DeepSeek/Gemini/Kimi
    native_openai.py  原生OpenAI 前缀: gpt- o1- o3-  支持: GPT-4o/o1/o3
    anthropic.py      Anthropic  前缀: claude- c_anth/ 特性: 覆盖 call_completion/extract_content（协议不同）
    ollama.py         Ollama本地 前缀: ollama/       无需 API key

添加新 Provider:
    1. 新建 .py 文件，继承 BaseProvider
    2. 实现 create_client() 和 _check()
    3. 在 ALL_PROVIDERS 列表中注册
    4. 在 config.local.json 的 providers 中加入凭证
"""
from api.providers.xfyun import XfyunProvider
from api.providers.openai_compat import OpenAICompatProvider
from api.providers.native_openai import NativeOpenAIProvider
from api.providers.anthropic import AnthropicProvider
from api.providers.ollama import OllamaProvider

ALL_PROVIDERS = [
    XfyunProvider(),
    OpenAICompatProvider(),
    NativeOpenAIProvider(),
    AnthropicProvider(),
    OllamaProvider(),
]
