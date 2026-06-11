import json
import os
import re
from typing import Any
from lib.token_tracker import track_token_usage

import anthropic
import backoff
import openai

MAX_NUM_TOKENS = 128000  # Max supported by Xiaomi API

AVAILABLE_LLMS = [
    "claude-3-5-sonnet-20240620",
    "claude-3-5-sonnet-20241022",
    # OpenAI models
    "gpt-4o-mini",
    "gpt-4o-mini-2024-07-18",
    "gpt-4o",
    "gpt-4o-2024-05-13",
    "gpt-4o-2024-08-06",
    "gpt-4.1",
    "gpt-4.1-2025-04-14",
    "gpt-4.1-mini",
    "gpt-4.1-mini-2025-04-14",
    "o1",
    "o1-2024-12-17",
    "o1-preview-2024-09-12",
    "o1-mini",
    "o1-mini-2024-09-12",
    "o3-mini",
    "o3-mini-2025-01-31",
    # DeepSeek Models
    "deepseek-coder-v2-0724",
    "deepcoder-14b",
    # Llama 3 models
    "llama3.1-405b",
    # Anthropic Claude models via Amazon Bedrock
    "bedrock/anthropic.claude-3-sonnet-20240229-v1:0",
    "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
    "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
    "bedrock/anthropic.claude-3-haiku-20240307-v1:0",
    "bedrock/anthropic.claude-3-opus-20240229-v1:0",
    # Anthropic Claude models Vertex AI
    "vertex_ai/claude-3-opus@20240229",
    "vertex_ai/claude-3-5-sonnet@20240620",
    "vertex_ai/claude-3-5-sonnet@20241022",
    "vertex_ai/claude-3-sonnet@20240229",
    "vertex_ai/claude-3-haiku@20240307",
    # Google Gemini models
    "gemini-2.0-flash",
    "gemini-2.5-flash-preview-04-17",
    "gemini-2.5-pro-preview-03-25",
    # GPT-OSS models via Ollama
    "ollama/gpt-oss:20b",
    "ollama/gpt-oss:120b",
    # Qwen models via Ollama
    "ollama/qwen3:8b",
    "ollama/qwen3:32b",
    "ollama/qwen3:235b",

    "ollama/qwen2.5vl:8b",
    "ollama/qwen2.5vl:32b",

    "ollama/qwen3-coder:70b",
    "ollama/qwen3-coder:480b",

    # Deepseek models via Ollama
    "ollama/deepseek-r1:8b",
    "ollama/deepseek-r1:32b",
    "ollama/deepseek-r1:70b",
    "ollama/deepseek-r1:671b",
    # Custom OpenAI-compatible (via environment variables)
    "custom/mimo-v2.5-pro",
    "custom/mimo-v2.5",
    "custom/mimo-v2-omni",
    "custom2/deepseek-v4-pro",
    "custom2/deepseek-v4-flash",
    "custom2/kimi-k2.6",
    "custom2/kimi-k2.5",
    "custom2/mimo-v2.5-pro",
    "custom2/glm-5.1",
    # Kimi Code OpenAI endpoint (User-Agent spoofed)
    "kimi_oai/kimi-k2.6",
    # Custom Anthropic-compatible (via environment variables)
    "c_anth/kimi-k2.6",
    "c_anth/kimi-for-coding",
    # Xunfei Astron Coding Plan (OpenAI-compatible)
    "xfyun/astron-code-latest",
]


# Get N responses from a single message, used for ensembling.
@backoff.on_exception(
    backoff.expo,
    (
        openai.RateLimitError,
        openai.APITimeoutError,
        openai.InternalServerError,
        anthropic.RateLimitError,
    ),
    max_tries=5,
    max_time=300,
)
@track_token_usage
def get_batch_responses_from_llm(
    prompt,
    client,
    model,
    system_message,
    print_debug=False,
    msg_history=None,
    temperature=0.7,
    n_responses=1,
) -> tuple[list[str], list[list[dict[str, Any]]]]:
    from api import call_completion, extract_content, is_anthropic

    if msg_history is None:
        msg_history = []

    if is_anthropic(model):
        new_msg_history = msg_history + [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
        response = call_completion(model, client, model, messages=new_msg_history,
                                  temperature=temperature, max_tokens=MAX_NUM_TOKENS,
                                  system_message=system_message)
        content = extract_content(model, response)
        new_msg_history = [new_msg_history + [{"role": "assistant", "content": [{"type": "text", "text": c}]}] for c in content]
    else:
        new_msg_history = msg_history + [{"role": "user", "content": prompt}]
        messages = [{"role": "system", "content": system_message}, *new_msg_history]
        response = call_completion(model, client, model, messages=messages,
                                  temperature=temperature, max_tokens=MAX_NUM_TOKENS,
                                  n=n_responses, seed=0)
        content = extract_content(model, response)
        new_msg_history = [new_msg_history + [{"role": "assistant", "content": c}] for c in content]

    if print_debug:
        print()
        print("*" * 20 + " LLM BATCH START " + "*" * 20)
        for j, m in enumerate(new_msg_history[0]):
            print(f'{j}, {m["role"]}: {m["content"]}')
        print(content)
        print("*" * 21 + " LLM BATCH END " + "*" * 21)
        print()

    return content, new_msg_history


@backoff.on_exception(
    backoff.expo,
    (
        openai.RateLimitError,
        openai.APITimeoutError,
        openai.InternalServerError,
        anthropic.RateLimitError,
    ),
    max_tries=5,
    max_time=300,
)
def get_response_from_llm(
    prompt,
    client,
    model,
    system_message,
    print_debug=False,
    msg_history=None,
    temperature=0.7,
    max_tokens=None,
) -> tuple[str, list[dict[str, Any]]]:
    from api import call_completion, extract_content, is_anthropic, get_registry

    if msg_history is None:
        msg_history = []

    provider = get_registry().get_provider(model)
    if provider is None:
        raise ValueError(f"Model {model} not handled by any provider.")
    actual_model = provider.strip_prefix(model)

    if max_tokens is not None:
        effective_max_tokens = max_tokens
    else:
        effective_max_tokens = MAX_NUM_TOKENS

    if provider.is_anthropic:
        new_msg_history = msg_history + [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
        response = provider.call_completion(
            client, actual_model, new_msg_history,
            temperature=temperature, max_tokens=effective_max_tokens,
            system_message=system_message,
        )
        content = provider.extract_content(response)[0]
        new_msg_history = new_msg_history + [{"role": "assistant", "content": [{"type": "text", "text": content}]}]
    else:
        new_msg_history = msg_history + [{"role": "user", "content": prompt}]
        messages = [{"role": "system", "content": system_message}, *new_msg_history]
        extra = {}
        if "o1" in model or "o3" in model:
            extra["seed"] = None
        response = provider.call_completion(
            client, actual_model, messages,
            temperature=temperature, max_tokens=effective_max_tokens,
            n=1, seed=0, **extra,
        )
        content = provider.extract_content(response)[0]
        new_msg_history = new_msg_history + [{"role": "assistant", "content": content}]

    if print_debug:
        print()
        print("*" * 20 + " LLM START " + "*" * 20)
        for j, m in enumerate(new_msg_history):
            print(f'{j}, {m["role"]}: {m["content"]}')
        print(content)
        print("*" * 21 + " LLM END " + "*" * 21)
        print()

    return content, new_msg_history


def extract_json_between_markers(llm_output: str) -> dict | None: 
    # Regular expression pattern to find JSON content between ```json and ```
    json_pattern = r"```json(.*?)```"
    matches = re.findall(json_pattern, llm_output, re.DOTALL)

    if not matches:
        # Fallback: find matching braces for nested JSON
        start = llm_output.find("{")
        if start >= 0:
            depth = 0
            in_string = False
            escape = False
            for i in range(start, len(llm_output)):
                c = llm_output[i]
                if escape:
                    escape = False
                    continue
                if c == "\\" and in_string:
                    escape = True
                    continue
                if c == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        matches = [llm_output[start:i+1]]
                        break

    for json_string in matches:
        json_string = json_string.strip()
        try:
            parsed_json = json.loads(json_string)
            return parsed_json
        except json.JSONDecodeError:
            # Attempt to fix common JSON issues
            try:
                # Remove invalid control characters
                json_string_clean = re.sub(r"[\x00-\x1F\x7F]", "", json_string)
                parsed_json = json.loads(json_string_clean)
                return parsed_json
            except json.JSONDecodeError:
                continue  # Try next match

    return None  # No valid JSON found


def create_client(model) -> tuple[Any, str]:
    from api import create_client as _api_create
    client, _actual, original = _api_create(model)
    return client, original
