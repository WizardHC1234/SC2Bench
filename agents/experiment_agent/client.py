"""Independent OpenAI-compatible transport; no dependency on the test llm_agent.

Native thinking is selected through an explicit provider profile. No endpoint,
credential or HTTP error body is included in returned records.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from http.client import HTTPException
import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .config import PROVIDERS
from .protocol import parse_json


def build_payload(*, model, provider, messages, tools=None, max_output_tokens=None, temperature=0.5,
                  response_format=None, tool_choice=None, native_thinking=False):
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {PROVIDERS}")
    payload = {
        "model": model, "messages": deepcopy(messages), "stream": False,
        "temperature": temperature,
    }
    if max_output_tokens is not None:
        payload["max_tokens"] = max_output_tokens
    if response_format is not None:
        payload["response_format"] = deepcopy(response_format)
    if provider == "deepseek":
        payload["thinking"] = {"type": "enabled" if native_thinking else "disabled"}
        if native_thinking and tool_choice not in (None, "auto"):
            raise ValueError("DeepSeek thinking supports tool_choice=auto only")
    elif provider == "deepseek_sglang":
        payload["chat_template_kwargs"] = {"thinking": native_thinking}
    elif provider == "qwen_vllm":
        payload["chat_template_kwargs"] = {"enable_thinking": native_thinking}
    else:
        payload["enable_thinking"] = native_thinking
    if tools:
        payload.update(tools=deepcopy(tools), tool_choice=tool_choice or "auto")
    return payload


def parse_completion(completion):
    choice = completion["choices"][0]
    message = choice["message"]
    content = message.get("content") or ""
    if not isinstance(content, str):
        raise ValueError("non-text completion")
    calls = []
    for item in message.get("tool_calls") or []:
        function = item["function"]
        # Keep malformed JSON as a string for the Agent to reject visibly.
        arguments = function.get("arguments", "")
        if isinstance(arguments, str):
            try:
                arguments = parse_json(arguments)
            except ValueError:
                pass
        calls.append({"id": item["id"], "name": function["name"], "arguments": arguments})
    return {
        "content": content, "tool_calls": calls,
        "model": completion.get("model"), "usage": completion.get("usage") or {},
        "finish_reason": choice.get("finish_reason"),
        "reasoning": message.get("reasoning_content") or message.get("reasoning") or "",
    }


@dataclass(frozen=True)
class ChatClient:
    model: str
    provider: str
    base_url: str = field(repr=False)
    api_key: str = field(repr=False)
    temperature: float = 0.5
    timeout: float = 120.0
    native_thinking: bool = False

    def __post_init__(self):
        if type(self.native_thinking) is not bool:
            raise ValueError("native_thinking must be a boolean")
        parsed = urlsplit(self.base_url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("base_url must be an HTTP(S) URL without credentials, query or fragment")
        if not self.model.strip() or not self.api_key.strip() or self.provider not in PROVIDERS:
            raise ValueError("model, provider and API key are required")
        if not math.isfinite(self.temperature) or not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be in [0, 2]")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout must be positive and finite")

    def __call__(self, messages, *, tools=None, max_output_tokens=None, response_format=None, tool_choice=None):
        payload = build_payload(
            model=self.model, provider=self.provider, messages=messages, tools=tools,
            temperature=self.temperature, max_output_tokens=max_output_tokens,
            response_format=response_format,
            tool_choice=tool_choice,
            native_thinking=self.native_thinking,
        )
        request = Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key},
            method="POST",
        )
        start = time.perf_counter()
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = parse_completion(parse_json(response.read().decode("utf-8")))
            result["model"] = result.get("model") or self.model
        except (URLError, HTTPException, OSError, ValueError, KeyError, IndexError, TypeError) as error:
            status = error.code if isinstance(error, HTTPError) else None
            retryable = status in (408, 429) or (status is not None and status >= 500)
            if status is None:
                retryable = isinstance(error, (URLError, HTTPException, OSError))
            result = {"error": type(error).__name__, "http_status": status,
                      "retryable": retryable, "model": self.model}
        result["latency_seconds"] = time.perf_counter() - start
        return result
