"""Session state and tool loop for the example SC2Bench LLM agent."""

from __future__ import annotations

import json
import math
import time
from http.client import HTTPException
from typing import Any, Callable, Mapping, Optional

from sc2bench_env.benchmark import AgentInput, AgentStopped, AgentTurn
from sc2bench_env.interface.tools import (
    KNOWLEDGE_TOOLS,
    READ_TOOLS,
    NormalizedToolCall,
    queued_tool_result,
    render_tool_result,
)

from .client import make_llm_call
from .config import (
    SAFE_API_ERROR_TYPES,
    llm_api_key,
    llm_base_url,
    llm_model,
)
from .prompts import (
    ACTION_BATCH_HINT,
    MAX_TOOL_ROUNDS,
    NO_TOOL_HINT,
    TOOL_NOTE_HINT,
    TOOL_NOTE_RULE,
    action_names,
    reply_kind,
)
from .skills import DEFAULT_SKILL, load_skill, skill_race


class InvalidDecisionLimit(AgentStopped):
    def __init__(self, message: str = "consecutive invalid decisions") -> None:
        super().__init__("invalid_decision_limit")


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
            available_actions = action_names(schemas)
            if reply_kind(names, available_actions) == "invalid":
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
                if name in available_actions:
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
        if self.skill_name:
            actual_race = str(request.observation.race).strip().lower()
            expected_race = skill_race(self.skill_name)
            if actual_race != expected_race:
                raise ValueError(
                    f"skill {self.skill_name!r} requires race {expected_race!r}, "
                    f"but the episode race is {actual_race!r}"
                )
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
    """Run the example module entry point."""
    from .cli import main as cli_main

    return cli_main(argv)
