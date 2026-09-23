"""LLM Agent with an optional whole-match strategy skill."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from sc2bench_env.benchmark import AgentInput, AgentStopped, AgentTurn, BenchmarkRunner
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.opponents import (
    BUILTIN_OPPONENTS,
    ENEMY_STYLES,
    normalize_enemy_style,
    normalize_opponent,
)
from sc2bench_env.interface.races import ENEMY_RACES, SUPPORTED_OWN_RACES
from sc2bench_env.interface.tools import (
    ACTION_TOOLS, KNOWLEDGE_TOOLS, READ_TOOLS, NormalizedToolCall,
    queued_tool_result, render_tool_result,
)

from .skills import AVAILABLE_SKILLS, DEFAULT_SKILL, load_skill


MAX_TOOL_ROUNDS = 24
TOOL_NOTE_RULE = (
    "Before every tool call, write a short note in the reply content: "
    "which fact you are using and why this call is next. "
    "Do this for queries and for actions. The note is not an action."
)
TOOL_NOTE_HINT = (
    "This reply was not submitted because it called tools without a content note. "
    "Write a short note first: which fact you are using and why this call is next. "
    "Then call the tools again."
)
NO_TOOL_HINT = (
    "Text without tool calls does not submit actions. "
    "Put the short content note in the same reply as the tool calls. "
    "Query across as many rounds as you need. "
    "When you act, put every action for this decision in one reply and end it with exactly one advance."
)
ACTION_BATCH_HINT = (
    "This reply was not submitted. Finish queries before acting. "
    "A query reply contains only query tools. "
    "An action reply contains only actions, includes every action for this decision, "
    "and ends with exactly one advance."
)
_QUERY_TOOLS = frozenset(READ_TOOLS) | frozenset(KNOWLEDGE_TOOLS)
_ACTION_TOOL_NAMES = frozenset(ACTION_TOOLS)


def _action_names(schemas) -> frozenset[str]:
    """Actions this race was actually offered, including race-only verbs."""
    names = set(_ACTION_TOOL_NAMES)
    for schema in schemas or ():
        function = schema.get("function") if isinstance(schema, dict) else None
        if isinstance(function, dict) and function.get("name"):
            names.add(str(function["name"]))
    return frozenset(names - _QUERY_TOOLS)


def _reply_kind(names: list[str], action_names: frozenset[str]) -> str:
    """A decision may query over several rounds, then submit one complete action reply."""
    if names and all(name in _QUERY_TOOLS for name in names):
        return "query"
    if (
        names
        and all(name in action_names for name in names)
        and names.count("advance") == 1
        and names[-1] == "advance"
    ):
        return "action"
    return "invalid"
DEFAULT_API_KEY = "EMPTY"
DEFAULT_API_BASE_URL = "http://172.18.132.20:2325/v1"
DEFAULT_MODEL = "DeepSeek-V4-Flash"
DEFAULT_TEMPERATURE = 0.5
SAFE_API_ERROR_TYPES = frozenset({
    "HTTPError", "URLError", "TimeoutError", "ConnectionError", "OSError",
    "SSLError", "RemoteDisconnected", "ConnectionResetError",
    "ConnectionAbortedError", "BrokenPipeError", "IncompleteRead",
    "BadStatusLine", "HTTPException", "JSONDecodeError", "UnicodeDecodeError",
    "ValueError", "KeyError", "IndexError", "TypeError", "RuntimeError",
})


class InvalidDecisionLimit(AgentStopped):
    def __init__(self, message: str = "consecutive invalid decisions") -> None:
        super().__init__("invalid_decision_limit")


def _positive_number(value: str, *, integer: bool) -> int | float:
    try:
        number = int(value) if integer else float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "must be a positive number") from error
    if integer:
        if type(number) is not int or number < 1:
            raise argparse.ArgumentTypeError("must be a positive integer")
        return number
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return number


def positive_int(value: str) -> int:
    return int(_positive_number(value, integer=True))


def positive_float(value: str) -> float:
    return float(_positive_number(value, integer=False))


def parse_temperature(value: str) -> float:
    try:
        number = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("temperature must be >= 0")
    return number


def env_setting(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def llm_api_key() -> str:
    return env_setting("LLM_API_KEY", DEFAULT_API_KEY)


def llm_base_url() -> str:
    return env_setting("LLM_BASE_URL", DEFAULT_API_BASE_URL)


def llm_model() -> str:
    return env_setting("LLM_MODEL", DEFAULT_MODEL)


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
        payload: dict[str, Any] = {
            "model": model, "messages": messages, "temperature": temperature,
            "stream": False,
            "chat_template_kwargs": {"thinking": thinking, "enable_thinking": thinking},
            "response_format": {"type": "text"},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
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


def _print_block(title: str, content: str) -> None:
    print(f"\n[{title}]\n{content}", flush=True)


def _pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _tool_query_title(name: str) -> str:
    if name in KNOWLEDGE_TOOLS:
        return "Knowledge query"
    if name in READ_TOOLS:
        return "Read query"
    return "Tool call"


class LLMAgent:
    """One instance serves one episode and keeps that episode's messages."""

    def __init__(
        self, call_llm: Callable[..., dict[str, Any]],
        *, max_api_attempts: int = 3, api_retry_delay_seconds: float = 1.0,
        max_consecutive_rejections: int = 3, thinking_requested: bool = False,
        verbose: bool = True, skill_name: Optional[str] = DEFAULT_SKILL,
    ) -> None:
        if (type(max_api_attempts) is not int or max_api_attempts < 1
                or not math.isfinite(api_retry_delay_seconds) or api_retry_delay_seconds < 0
                or type(max_consecutive_rejections) is not int or max_consecutive_rejections < 1):
            raise ValueError(
                "invalid external Agent retry or rejection limits")
        self.call_llm = call_llm
        self.max_api_attempts = max_api_attempts
        self.api_retry_delay_seconds = api_retry_delay_seconds
        self.max_consecutive_rejections = max_consecutive_rejections
        self.thinking_requested = thinking_requested
        self.verbose = verbose
        loaded_skill = load_skill(skill_name)
        self.skill_name = loaded_skill[0] if loaded_skill else None
        self.skill_text = loaded_skill[1] if loaded_skill else ""
        self.messages: list[dict[str, Any]] = []
        self.rejected = 0

    def __call__(self, request: AgentInput) -> AgentTurn:
        self._append_turn_input(request)
        if request.feedback is not None:
            reasons = [str(event.get("reason", "unknown"))
                       for event in request.feedback.events
                       if event.get("type") == "decision_rejected"]
            if reasons:
                self.rejected += 1
                if self.rejected >= self.max_consecutive_rejections:
                    raise InvalidDecisionLimit("consecutive invalid decisions")
            else:
                self.rejected = 0

        if self.verbose:
            for message in self.messages[-1:]:
                if message.get("role") == "user":
                    _print_block("Agent input", message.get("content") or "")

        schemas = list(request.tool_schemas or ())
        self._sent_tools = schemas
        queued: list[dict[str, Any]] = []
        provider_tool_calls: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        last_attempt = 1
        for _round in range(MAX_TOOL_ROUNDS):
            response, attempt, round_failures = self._complete_once(schemas)
            last_attempt = attempt
            failures.extend(round_failures)
            if response.get("error"):
                return AgentTurn(None, call_failures=tuple(failures),
                                 stop_after_call_failures=True)
            tool_calls = list(response.get("tool_calls") or [])
            raw = response.get("raw_content", response.get("content", ""))
            content = response.get("content") or ""
            if not tool_calls:
                self.messages.append(
                    {"role": "assistant", "content": content or raw})
                if self.verbose:
                    if response.get("reasoning"):
                        _print_block("Model reasoning (API returned)",
                                     str(response.get("reasoning") or ""))
                    _print_block("Model reply (raw)", raw or "(empty)")
                    _print_block("Tool reminder", NO_TOOL_HINT)
                self.messages.append({"role": "user", "content": NO_TOOL_HINT})
                continue
            if not str(content).strip():
                self.messages.append(
                    self._assistant_tool_message(content, raw, tool_calls))
                if self.verbose:
                    _print_block("Tool reminder", TOOL_NOTE_HINT)
                self._reject_tool_reply(tool_calls, TOOL_NOTE_HINT)
                continue
            self.messages.append(
                self._assistant_tool_message(content, raw, tool_calls))
            if self.verbose:
                if response.get("reasoning"):
                    _print_block("Model reasoning (API returned)",
                                 str(response.get("reasoning") or ""))
                if content:
                    _print_block("Model note", content)
                print(f"Tool round {_round + 1}: {len(tool_calls)} call(s)",
                      flush=True)
            names = [str(call.get("name") or "") for call in tool_calls]
            action_names = _action_names(schemas)
            if _reply_kind(names, action_names) == "invalid":
                if self.verbose:
                    _print_block("Tool reminder", ACTION_BATCH_HINT)
                self._reject_tool_reply(tool_calls, ACTION_BATCH_HINT)
                continue
            finished = False
            for index, call in enumerate(tool_calls):
                name = str(call.get("name") or "")
                arguments = call.get("arguments") if isinstance(
                    call.get("arguments"), dict) else {}
                call_id = str(
                    call.get("id") or f"call_{len(self.messages)}_{index}")
                normalized = NormalizedToolCall(name, dict(arguments)).to_dict()
                provider_tool_calls.append({
                    "id": call_id, "name": name, "arguments": dict(arguments),
                })
                if name in action_names:
                    queued.append(normalized)
                    result = queued_tool_result(NormalizedToolCall(name, dict(arguments)))
                    rendered_result = render_tool_result(name, result)
                    self.messages.append({
                        "role": "tool", "tool_call_id": call_id,
                        "content": rendered_result,
                    })
                    if self.verbose:
                        _print_block("Queued action", _pretty_json(normalized))
                    if name == "advance":
                        finished = True
                        break
                    continue
                if self.verbose:
                    _print_block(
                        _tool_query_title(name),
                        f"{name} {_pretty_json(arguments)}",
                    )
                if request.call_tool is None:
                    result = {"error": "tools_unavailable"}
                else:
                    result = request.call_tool(name, arguments)
                rendered_result = render_tool_result(name, result)
                if self.verbose:
                    _print_block("Tool result", rendered_result)
                self.messages.append({
                    "role": "tool", "tool_call_id": call_id,
                    "content": rendered_result,
                })
            if finished:
                context = self._turn_context(
                    response, request, last_attempt, failures)
                context["normalized_tool_calls"] = list(queued)
                context["provider_tool_calls"] = list(provider_tool_calls)
                if self.verbose:
                    _print_block("Submitted actions", _pretty_json(queued))
                print(
                    f"LLM game_seconds={request.observation.game.game_time_seconds:.1f} "
                    f"attempt={last_attempt} parsed=True repaired=False tools={len(queued)}",
                    flush=True,
                )
                return AgentTurn(queued, context, call_failures=tuple(failures))
        context = {"messages": list(self.messages), "tools": list(self._sent_tools),
                   "assistant_content": "", "error": "tool_round_limit",
                   "normalized_tool_calls": queued,
                   "provider_tool_calls": provider_tool_calls}
        return AgentTurn(None, context, call_failures=tuple(failures))

    def _complete_once(self, schemas: list[dict[str, Any]]) -> tuple[dict[str, Any], int, list[dict[str, Any]]]:
        response: dict[str, Any] = {}
        failures: list[dict[str, Any]] = []
        attempt = 1
        for attempt in range(1, self.max_api_attempts + 1):
            started = time.perf_counter()
            try:
                response = self._invoke_llm(schemas)
                if not isinstance(response, dict):
                    raise TypeError(
                        "LLM callback must return a response dictionary")
            except Exception as error:
                response = {
                    "error": type(error).__name__,
                    "retryable": isinstance(error, (OSError, HTTPException)),
                    "latency_seconds": time.perf_counter() - started,
                }
            if not response.get("error"):
                break
            error_type = response.get("error")
            status = response.get("http_status")
            failures.append({
                "messages": self.messages, "tools": list(self._sent_tools),
                "assistant_content": "",
                "model": response.get("model", llm_model()),
                "thinking_requested": self.thinking_requested,
                "latency_seconds": response.get("latency_seconds", 0),
                "api_failed": True, "api_attempt": attempt,
                "api_error_type": error_type if isinstance(error_type, str)
                and error_type in SAFE_API_ERROR_TYPES else "unknown",
                "api_http_status": status if type(status) is int and 100 <= status <= 599 else None,
                "api_retryable": bool(response.get("retryable", True)),
            })
            failure = failures[-1]
            print(
                f"LLM API attempt {attempt}/{self.max_api_attempts} failed: "
                f"{failure['api_error_type']}; HTTP={failure['api_http_status'] or 'n/a'}; "
                f"retryable={failure['api_retryable']}",
                flush=True,
            )
            if attempt == self.max_api_attempts or not response.get("retryable", True):
                return response, attempt, failures
            if self.api_retry_delay_seconds:
                time.sleep(min(60.0, self.api_retry_delay_seconds * attempt))
        return response, attempt, failures

    def _invoke_llm(self, schemas: list[dict[str, Any]]) -> dict[str, Any]:
        messages = [dict(message) for message in self.messages]
        if schemas:
            try:
                return self.call_llm(messages, tools=schemas)
            except TypeError:
                return self.call_llm(messages)
        return self.call_llm(messages)

    def _assistant_tool_message(self, content: str, raw: str, tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
        serialized = []
        for index, call in enumerate(tool_calls):
            arguments = call.get("arguments") if isinstance(
                call.get("arguments"), dict) else {}
            serialized.append({
                "id": str(call.get("id") or f"call_{len(self.messages)}_{index}"),
                "type": "function",
                "function": {
                    "name": str(call.get("name") or ""),
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            })
        return {"role": "assistant", "content": content or raw or None, "tool_calls": serialized}

    def _turn_context(
        self, response: Mapping[str, Any], request: AgentInput, attempt: int,
        failures: list[dict[str, Any]],
    ) -> dict[str, Any]:
        _ = request, failures
        raw = response.get("raw_content", response.get("content", ""))
        return {
            "messages": [dict(message) for message in self.messages],
            "tools": list(self._sent_tools),
            "assistant_content": raw,
            "model": response.get("model", llm_model()),
            "usage": response.get("usage") or {},
            "reasoning": response.get("reasoning") or "",
            "finish_reason": response.get("finish_reason") or "",
            "thinking_requested": response.get("thinking_requested", self.thinking_requested),
            "latency_seconds": response.get("latency_seconds", 0),
            "api_attempt": attempt,
        }

    def _reject_tool_reply(self, tool_calls: list[dict[str, Any]], reason: str) -> None:
        for index, call in enumerate(tool_calls):
            call_id = str(call.get("id") or f"call_{len(self.messages)}_{index}")
            self.messages.append({
                "role": "tool", "tool_call_id": call_id,
                "content": f"status: not_submitted\nreason: {reason}",
            })

    def _append_turn_input(self, request: AgentInput) -> None:
        incoming = [dict(message) for message in request.platform_messages]
        if not incoming:
            raise ValueError("platform_messages must not be empty")
        if not self.messages:
            self.messages = incoming
            system = self.messages[0]
            if system.get("role") == "system":
                content = str(system.get("content") or "").rstrip()
                if self.skill_text:
                    content += (
                        f"\n\n[Agent Skill: {self.skill_name}]\n"
                        f"{self.skill_text}\n"
                    )
                if TOOL_NOTE_RULE not in content:
                    content += f"\n\n{TOOL_NOTE_RULE}\n"
                system["content"] = content
            return
        user = next((message for message in reversed(incoming)
                    if message.get("role") == "user"), None)
        if user is None:
            raise ValueError(
                "platform_messages must include a user Observation")
        self.messages.append(user)


def create_agent(call_llm=None, **agent_options) -> LLMAgent:
    """Return a new episode Agent. Inject call_llm in tests; otherwise use env or Commander defaults."""
    if call_llm is None:
        call_llm = make_llm_call(
            api_key=llm_api_key(),
            base_url=llm_base_url(),
            model=llm_model(),
        )
    return LLMAgent(call_llm, **agent_options)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Minimal SC2Bench LLM Agent. One instance keeps one episode session.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--opponent", "--difficulty", type=normalize_opponent,
                        choices=BUILTIN_OPPONENTS, default="easy")
    parser.add_argument("--race", choices=SUPPORTED_OWN_RACES, default="terran",
                        help="own race against the built-in computer")
    parser.add_argument("--enemy-race", choices=ENEMY_RACES, default="terran")
    parser.add_argument("--enemy-style", type=normalize_enemy_style,
                        choices=ENEMY_STYLES, default="random")
    parser.add_argument("--map", default="KairosJunctionLE")
    parser.add_argument("--game-time-limit", type=positive_float, default=1800)
    parser.add_argument("--max-decisions", type=positive_int, default=500)
    parser.add_argument("--max-rejections", type=positive_int, default=3)
    parser.add_argument("--api-timeout", type=positive_float, default=120)
    parser.add_argument("--api-attempts", type=positive_int, default=3)
    parser.add_argument("--api-retry-delay", type=positive_float, default=1)
    parser.add_argument("--temperature", type=parse_temperature, default=DEFAULT_TEMPERATURE)
    parser.add_argument(
        "--thinking", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--skill", choices=(*AVAILABLE_SKILLS, "none"), default=DEFAULT_SKILL,
        help="whole-match strategy skill; use 'none' for the no-skill baseline",
    )
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--record-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.race != "terran" and args.skill != "none":
        parser.error(
            f"{args.skill} is a Terran strategy; use --skill none for {args.race}"
        )
    config = EpisodeConfig(
        race=args.race, enemy_race=args.enemy_race, map_name=args.map,
        enemy_style=args.enemy_style, opponent=args.opponent,
        blocking_decisions=True, game_time_limit_seconds=args.game_time_limit,
    )
    if args.dry_run:
        from sc2bench_env.paths import resolve_record_dir
        print(json.dumps({
            "episode": config.to_dict(),
            "model": llm_model(),
            "base_url": llm_base_url(),
            "skill": args.skill,
            "max_decisions": args.max_decisions,
            "record_dir": str(resolve_record_dir(args.record_dir)),
        }, ensure_ascii=False, indent=2))
        return 0
    call_llm = make_llm_call(
        api_key=llm_api_key(),
        base_url=llm_base_url(),
        model=llm_model(),
        timeout=args.api_timeout,
        temperature=args.temperature,
        thinking=args.thinking,
    )
    runner = BenchmarkRunner(record_dir=args.record_dir)
    batch = runner.run(
        [config],
        lambda: LLMAgent(
            call_llm, max_api_attempts=args.api_attempts,
            api_retry_delay_seconds=args.api_retry_delay,
            max_consecutive_rejections=args.max_rejections,
            thinking_requested=args.thinking, verbose=not args.quiet,
            skill_name=args.skill,
        ),
        max_decisions=args.max_decisions,
    )
    print(json.dumps(batch["aggregate"], ensure_ascii=False), flush=True)
    return 0 if all(row["status"] == "completed" for row in batch["episodes"]) else 1
