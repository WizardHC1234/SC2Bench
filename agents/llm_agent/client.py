"""OpenAI-compatible chat-completions client used by the example agent."""

from __future__ import annotations

import json
import re
import time
from http.client import HTTPException
from typing import Any, Callable, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .config import DEFAULT_API_BASE_URL, DEFAULT_MODEL

def split_reasoning(text: str) -> tuple[str, str]:
    pattern = r"<think\b[^>]*>(.*?)</think>"
    thoughts = re.findall(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(pattern, "", text, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"<think\b[^>]*>.*", "", content,
                     flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"</think\s*>", "\n\n", content, flags=re.IGNORECASE)
    return content.strip(), "\n\n".join(thought.strip() for thought in thoughts)


def _parse_tool_calls(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    parsed = []
    for item in message.get("tool_calls") or []:
        if not isinstance(item, Mapping):
            continue
        function = item.get("function") if isinstance(
            item.get("function"), Mapping) else item
        arguments = function.get("arguments") if isinstance(
            function, Mapping) else {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"_raw": arguments}
        parsed.append({
            "id": str(item.get("id") or ""),
            "name": str(function.get("name") or item.get("name") or ""),
            "arguments": arguments if isinstance(arguments, dict) else {},
            "raw": item,
        })
    return parsed


def build_chat_payload(
    *, model: str, messages: list[dict[str, Any]], temperature: float, thinking: bool,
    tools: Optional[list] = None,
) -> dict[str, Any]:
    """OpenAI-compatible body. Kimi K2.5 rejects the DeepSeek thinking fields."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if "kimi" in model.lower():
        # Kimi K2.5 only accepts 0.6 with thinking off, and 1 with thinking on.
        payload["temperature"] = 1.0 if thinking else 0.6
        payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        payload["chat_template_kwargs"] = {"thinking": thinking}
    else:
        payload["chat_template_kwargs"] = {"thinking": thinking, "enable_thinking": thinking}
        payload["response_format"] = {"type": "text"}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    return payload


def make_llm_call(
    *, api_key: str, base_url: str = DEFAULT_API_BASE_URL, model: str = DEFAULT_MODEL,
    timeout: float = 120, temperature: float = 0.5, thinking: bool = False,
) -> Callable[..., dict[str, Any]]:
    if not api_key.strip():
        raise ValueError("LLM_API_KEY is required")
    if not base_url.strip() or not model.strip():
        raise ValueError("LLM_BASE_URL and LLM_MODEL are required")
    endpoint = base_url.rstrip("/") + "/chat/completions"
    parsed_url = urlsplit(endpoint)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise ValueError("API gateway must be an http:// or https:// address")
    if parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment:
        raise ValueError(
            "API gateway must not contain URL credentials, query or fragment")

    def call(messages: list[dict[str, Any]], tools: Optional[list] = None, **_ignored: Any) -> dict[str, Any]:
        payload = build_chat_payload(
            model=model, messages=messages, temperature=temperature,
            thinking=thinking, tools=tools,
        )
        request = Request(
            endpoint, data=json.dumps(
                payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urlopen(request, timeout=timeout) as http_response:
                completion = json.loads(http_response.read().decode("utf-8"))
            choice = completion["choices"][0]
            message = choice["message"]
            raw = message.get("content") or ""
            if raw and not isinstance(raw, str):
                raise ValueError("message.content is not text")
            content, thoughts = split_reasoning(
                raw if isinstance(raw, str) else "")
            tool_calls = _parse_tool_calls(
                message if isinstance(message, Mapping) else {})
            return {
                "content": content, "raw_content": raw if isinstance(raw, str) else "",
                "reasoning": message.get("reasoning_content") or message.get("reasoning") or thoughts,
                "model": completion.get("model") or model,
                "usage": completion.get("usage") or {},
                "finish_reason": choice.get("finish_reason") or "",
                "thinking_requested": thinking,
                "latency_seconds": round(time.perf_counter() - started, 6),
                "actual_messages": messages, "error": "",
                "tool_calls": tool_calls,
            }
        except (URLError, OSError, HTTPException, ValueError, KeyError, IndexError, TypeError) as exc:
            status = exc.code if isinstance(exc, HTTPError) else None
            retryable = (
                status in {408, 429} or (status is not None and status >= 500)
            ) if status is not None else isinstance(exc, (URLError, OSError, HTTPException))
            return {
                "content": "", "raw_content": "", "model": model,
                "thinking_requested": thinking, "actual_messages": messages,
                "error": type(exc).__name__, "http_status": status,
                "retryable": retryable,
                "latency_seconds": round(time.perf_counter() - started, 6),
                "tool_calls": [],
            }

    return call


