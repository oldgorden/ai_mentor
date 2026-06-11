"""统一凭证源：config.local.json → config.json → 环境变量"""
import json
import os
from pathlib import Path
from typing import Optional

_MENTOR_DIR = Path(__file__).parent.parent / "mentor"
_CREDENTIALS: Optional[dict] = None


def _load_config_file() -> dict:
    for name in ("config.local.json", "config.json"):
        p = _MENTOR_DIR / name
        if p.exists():
            with open(p) as f:
                return json.load(f)
    return {}


def load_credentials() -> dict:
    """加载凭证，返回 {provider_name: {api_key, base_url, ...}} 字典"""
    global _CREDENTIALS
    if _CREDENTIALS is not None:
        return _CREDENTIALS

    config = _load_config_file()
    creds = {}

    # ── 从 config.json 的 providers 字段读取（新 schema） ──
    for name, pcfg in config.get("providers", {}).items():
        entry = {}
        if "api_key" in pcfg:
            entry["api_key"] = pcfg["api_key"]
        if "base_url" in pcfg:
            entry["base_url"] = pcfg["base_url"]
        if "extra" in pcfg:
            entry["extra"] = pcfg["extra"]
        if entry:
            creds[name] = entry

    # ── 向后兼容：从旧 schema 字段读取 ──
    student_cfg = config.get("student_config", {})
    if "custom" not in creds and student_cfg.get("api_key"):
        creds["custom"] = {
            "api_key": student_cfg["api_key"],
            "base_url": student_cfg.get("api_base", ""),
        }
    if "xfyun" not in creds and config.get("xfyun_api_key"):
        creds["xfyun"] = {
            "api_key": config["xfyun_api_key"],
            "base_url": config.get("xfyun_base_url", ""),
        }

    # ── 从环境变量读取（兜底，也能覆盖） ──
    env_map = {
        "custom": ("CUSTOM_OPENAI_API_KEY", "CUSTOM_OPENAI_BASE_URL"),
        "custom2": ("CUSTOM2_OPENAI_API_KEY", "CUSTOM2_OPENAI_BASE_URL"),
        "xfyun": ("XFYUN_API_KEY", "XFYUN_BASE_URL"),
        "anthropic": ("CUSTOM_ANTHROPIC_API_KEY", "CUSTOM_ANTHROPIC_BASE_URL"),
        "ollama": ("OLLAMA_API_KEY", None),
        "openai": ("OPENAI_API_KEY", None),
        "deepseek": ("DEEPSEEK_API_KEY", None),
        "gemini": ("GEMINI_API_KEY", None),
        "openrouter": ("OPENROUTER_API_KEY", None),
        "huggingface": ("HUGGINGFACE_API_KEY", None),
        "opencode-go": ("OPENCODE_GO_API_KEY", None),
    }
    for name, (key_env, url_env) in env_map.items():
        api_key = os.environ.get(key_env, "")
        if not api_key:
            continue
        if name not in creds:
            creds[name] = {}
        creds[name].setdefault("api_key", api_key)
        if url_env:
            base_url = os.environ.get(url_env, "")
            if base_url:
                creds[name].setdefault("base_url", base_url)

    # semantic scholar 单独处理
    s2_key = config.get("s2_api_key") or os.environ.get("S2_API_KEY", "")
    if s2_key:
        creds.setdefault("semantic_scholar", {})["api_key"] = s2_key

    _CREDENTIALS = creds
    return creds


def get_credential(provider: str) -> dict:
    creds = load_credentials()
    return creds.get(provider, {})


def get_s2_api_key() -> str:
    return get_credential("semantic_scholar").get("api_key", "")
