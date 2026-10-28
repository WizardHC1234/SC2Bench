"""Four independent research harnesses over the public SC2Bench Agent contract."""
from __future__ import annotations

from copy import deepcopy
from http.client import HTTPException
import json
import math
import re
import time
from uuid import uuid4

from sc2bench_env.adapters.llm import LLMAdapter
from sc2bench_env.interface.agent import AgentInput, AgentStopped, AgentTurn
from sc2bench_env.interface.tools import ToolCall, ToolResult

from .config import HarnessConfig
from . import prompts
from .protocol import validate_plan
from .trace import emit, reply_blocks


class _CallFailed(Exception):
    pass


def _token(usage, *names):
    for name in names:
        value = usage.get(name)
        if type(value) is int and value >= 0:
            return value
    return None


def _number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def contains_tool_markup(content):
    """Detect serialized call attempts; never parse them into executable tools."""
    return any(marker in content for marker in ("<｜DSML｜", "<tool_call", "<function=", "[TOOL_CALLS]"))


class ExperimentAgent:
    """One instance per game. The callback is shared by all of its modules.

    callback(messages, tools=..., max_output_tokens=..., response_format=...) returns a normalized
    content/tool_calls/model/usage dictionary (see client.parse_completion).
    No disk memory, replay truth, strategy skills or game restarts are used.
    """

    def __init__(self, call_llm, config: HarnessConfig | None = None, *, model: str | None = None):
        self.call_llm = call_llm
        self.config = config or HarnessConfig()
        self.model = model
        self.messages = []
        self.plan = []
        self.completed_steps = []
        self.current_step = None
        self.execution_history = []
        self.total_calls = 0
        self.known_input_tokens = 0
        self.known_output_tokens = 0
        self.missing_usage_calls = 0
        self._last_time = None
        self._race = None
        self._tools = []
        self._calls = []
        self._failures = []
        self._events = []
        self._pending_tool_calls = {}
        self._active_role = None

    def __call__(self, request: AgentInput) -> AgentTurn:
        self._calls, self._failures, self._events = [], [], []
        race = request.observation.game.race
        now = float(request.observation.game.game_time_seconds)
        self._game_time = now
        if self._race is not None and (race != self._race or (self._last_time is not None and now < self._last_time)):
            self._stop("episode_state_reused")
        self._race = race
        self._adapter = LLMAdapter(prompts.prompt_parts(race, self.config.harness), race=race)
        self._tools = self._adapter.render_tools(request.tool_specs)
        if not self.messages:
            self.messages.append({"role": "system", "content": self._adapter.system_prompt()})
        rendered_input = self._adapter.render_input(request)
        self.messages.append({"role": "user", "content": rendered_input})
        if self.execution_history and self.execution_history[-1]["observed_at"] is None:
            self.execution_history[-1]["observed_at"] = now
            self.execution_history[-1]["observation_message_index"] = len(self.messages) - 1
        emit(f"Observation and feedback | {self.config.harness} | game_time={now:.2f}s", rendered_input)
        try:
            fresh = self._last_time is None or now > self._last_time
            if fresh and self.config.harness == "plan_and_execute":
                self._update_plan(initial=self._last_time is None)
            elif fresh and self.config.harness == "reflection" and self._last_time is not None:
                self._text_stage("reflector", prompts.REFLECT)
            self._last_time = now
            if self.config.harness == "plan_and_execute":
                self.current_step = self.plan[0]
                self._instruction(prompts.EXECUTE + "\nCurrent remaining plan: " + json.dumps(self.plan, ensure_ascii=False)
                           + "\nYour current task is step 1: " + self.current_step, role="executor")
            elif self.config.harness != "act_only":
                self._instruction(prompts.ACT)
            return self._act(request)
        except _CallFailed:
            failure = self._failures[-1]
            self._user("[Harness control] Model request failed; episode stopped. "
                       f"role={failure['role']}; error={failure['error_type']}; HTTP={failure['http_status']}")
            return self._turn(stop_after_call_failures=True)

    def _user(self, content):
        self.messages.append({"role": "user", "content": content})
        if content.startswith("[Harness control]") or "[Harness feedback]" in content:
            emit("Harness feedback", content)

    def _instruction(self, content, *, role="actor"):
        # Append role changes to the actual transcript. Earlier observations,
        # assistant replies and native tool pairs remain intact and replayable.
        if self._active_role != role:
            self.messages.append({"role": "system", "content": prompts.ROLE_INSTRUCTIONS[role]})
            self._active_role = role
        # Keep a user task after the system role. Chat templates require a
        # generation turn after user/tool input, not after a bare system turn.
        self._user(content)

    def _context(self, stop_reason=None):
        context = {
            "messages": deepcopy(self.messages), "tools": deepcopy(self._tools),
            "session_scope": "episode", "model": self.model,
            "harness": self.config.harness, "thinking_requested": self.config.native_thinking,
            "agent_calls": deepcopy(self._calls), "protocol_events": deepcopy(self._events),
            "budget_usage": {"calls": self.total_calls, "known_input_tokens": self.known_input_tokens,
                             "known_output_tokens": self.known_output_tokens,
                             "missing_usage_calls": self.missing_usage_calls},
        }
        if stop_reason:
            context["stop_reason"] = stop_reason
        return context

    def _stop(self, reason):
        # Public AgentStopped supports a small fixed vocabulary. Keep the exact
        # cause in both the context and saved session: the compact platform
        # recorder does not persist arbitrary context fields in session.json.
        for call_id in list(self._pending_tool_calls):
            self._tool_response(call_id, json.dumps({"status": "not_submitted", "reason": reason}))
        self._user("[Harness control] Stopped this episode: " + reason)
        raise AgentStopped("agent_stopped", agent_context=self._context(reason))

    def _turn(self, stop_after_call_failures=False):
        context = self._context()
        failures = []
        for failure in self._failures:
            failures.append({
                "messages": context["messages"], "tools": context["tools"],
                "session_scope": "episode", "model": failure["model"],
                "role": failure["role"], "agent_calls": [deepcopy(failure)],
                "api_error_type": failure["error_type"], "api_http_status": failure["http_status"],
                "api_attempt": failure["attempt"],
            })
        return AgentTurn(context, tuple(failures), stop_after_call_failures)

    def _complete(self, role, tools=None):
        for attempt in range(1, self.config.max_api_attempts + 1):
            if (self.config.max_calls_per_decision is not None
                    and len(self._calls) >= self.config.max_calls_per_decision):
                self._stop("decision_call_budget")
            if (self.config.max_calls_per_episode is not None
                    and self.total_calls >= self.config.max_calls_per_episode):
                self._stop("episode_call_budget")
            output_cap = self.config.max_output_tokens
            if self.config.max_episode_output_tokens is not None:
                remaining = self.config.max_episode_output_tokens - self.known_output_tokens
                if remaining <= 0:
                    self._stop("episode_output_budget")
                output_cap = min(output_cap, remaining) if output_cap is not None else remaining
            self.total_calls += 1
            emit(f"Model call #{self.total_calls} | {role} | game_time={self._game_time:.2f}s",
                 f"model={self.model}; attempt={attempt}")
            started = time.perf_counter()
            try:
                options = {"tools": deepcopy(tools), "max_output_tokens": output_cap}
                if role in ("planner", "replanner"):
                    plan_format = getattr(self.call_llm, "plan_response_format", None)
                    options["response_format"] = (
                        plan_format() if callable(plan_format) else {"type": "json_object"}
                    )
                if tools and self.config.harness == "act_only":
                    options["tool_choice"] = "required"
                response = self.call_llm(deepcopy(self.messages), **options)
                if not isinstance(response, dict):
                    raise TypeError("callback must return a dictionary")
            except Exception as error:
                response = {"error": type(error).__name__, "retryable": isinstance(error, (OSError, HTTPException))}
            usage = response.get("usage")
            usage = usage if isinstance(usage, dict) else {}
            input_tokens = _token(usage, "prompt_tokens", "input_tokens")
            output_tokens = _token(usage, "completion_tokens", "output_tokens")
            error = response.get("error")
            safe_error = str(error) if error and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", str(error)) else "ModelCallError"
            status = response.get("http_status")
            if type(status) is not int or not 100 <= status <= 599:
                status = None
            actual_model = response.get("model")
            if isinstance(actual_model, str) and actual_model:
                self.model = actual_model
            record = {
                "call_id": uuid4().hex, "role": role, "model": self.model,
                "status": "failure" if error else "ok", "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_seconds": _number(response.get("latency_seconds")) if "latency_seconds" in response else time.perf_counter() - started,
                "error_type": safe_error if error else None, "http_status": status,
                "attempt": attempt,
            }
            self._calls.append(record)
            self.known_input_tokens += input_tokens or 0
            self.known_output_tokens += output_tokens or 0
            if input_tokens is None or output_tokens is None:
                self.missing_usage_calls += 1
            if error:
                emit("Model request failed", f"role={role}; error={safe_error}; HTTP={status}; attempt={attempt}")
                self._failures.append(record)
                if response.get("retryable", False) and attempt < self.config.max_api_attempts:
                    # Without output usage a strict episode token cap cannot be
                    # enforced even for a failed/ambiguous provider attempt.
                    if self.config.max_episode_output_tokens is not None and output_tokens is None:
                        raise _CallFailed()
                    time.sleep(min(30, self.config.retry_delay_seconds * 2 ** (attempt - 1)))
                    continue
                raise _CallFailed()
            content = response.get("content") or ""
            calls = response.get("tool_calls") or []
            if not isinstance(content, str) or not isinstance(calls, list):
                self._stop("malformed_model_response")
            response = {**response, "content": content, "tool_calls": calls}
            for title, value in reply_blocks(response, role):
                emit(title, value)
            reasoning = response.get("reasoning")
            if reasoning is not None and not isinstance(reasoning, str):
                self._stop("malformed_model_response")
            self._remember_reply(content, calls, reasoning)
            thought = re.search(r"<think>(.*?)(?:</think>|$)", content, flags=re.S)
            details = usage.get("completion_tokens_details")
            details = details if isinstance(details, dict) else {}
            native_tokens = _token(usage, "reasoning_tokens") or _token(details, "reasoning_tokens") or 0
            if not self.config.native_thinking and (reasoning or native_tokens or (thought and thought.group(1).strip())):
                self._stop("native_thinking_returned")
            if response.get("finish_reason") == "content_filter":
                self._stop("provider_content_filter")
            if self.config.max_episode_output_tokens is not None:
                if output_tokens is None:
                    self._stop("output_usage_missing")
                if self.known_output_tokens > self.config.max_episode_output_tokens:
                    self._stop("provider_output_budget_exceeded")
            if output_cap is not None and output_tokens is not None and output_tokens > output_cap:
                self._stop("provider_output_limit_exceeded")
            return response
        raise AssertionError("unreachable")

    def _remember_reply(self, content, calls, reasoning=None):
        if self._pending_tool_calls:
            self._stop("unresolved_tool_calls")
        message = {"role": "assistant", "content": content or None}
        if self.config.native_thinking or reasoning:
            message["reasoning_content"] = reasoning or ""
        if calls:
            serialized = []
            for call in calls:
                if (not isinstance(call, dict) or not isinstance(call.get("id"), str)
                        or not call["id"] or not isinstance(call.get("name"), str) or not call["name"]):
                    self._stop("malformed_tool_envelope")
                arguments = call.get("arguments")
                try:
                    serialized_arguments = arguments if isinstance(arguments, str) else json.dumps(
                        arguments, ensure_ascii=False, allow_nan=False)
                except (ValueError, TypeError):
                    self._stop("invalid_tool_arguments_encoding")
                serialized.append({"id": call["id"], "type": "function", "function": {
                    "name": call["name"], "arguments": serialized_arguments,
                }})
            if len({call["id"] for call in calls}) != len(calls):
                self._stop("duplicate_tool_call_ids")
            message["tool_calls"] = serialized
        self.messages.append(message)
        self._pending_tool_calls = {call["id"]: deepcopy(call) for call in calls}

    def _tool_response(self, call_id, content):
        self.messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
        call = self._pending_tool_calls.get(call_id, {})
        emit(f"Tool result | {call.get('name', 'unknown')} | {call_id}", content)
        self._pending_tool_calls.pop(call_id, None)

    def _reject_calls(self, calls, reason):
        self._events.append({"type": "not_submitted", "reason": reason})
        for call in calls:
            self._tool_response(call["id"], json.dumps({"status": "not_submitted", "reason": reason}))

    def _text_stage(self, role, instruction):
        self._instruction(instruction, role=role)
        while True:
            response = self._complete(role)
            calls = response.get("tool_calls") or []
            if calls:
                self._reject_calls(calls, "text_only_stage")
                self._instruction(prompts.stage_repair(instruction, prompts.TEXT_ONLY_STAGE), role=role)
            elif contains_tool_markup(response["content"]):
                self._events.append({"type": "not_submitted", "reason": "text_stage_tool_markup"})
                self._instruction(prompts.stage_repair(instruction, prompts.TOOL_MARKUP_REPAIR), role=role)
            elif not response.get("content", "").strip() or response.get("finish_reason") == "length":
                self._instruction(prompts.stage_repair(instruction, "The previous response was empty or truncated. Return a complete response in this stage's format."), role=role)
            else:
                return response["content"]

    def _update_plan(self, *, initial):
        role = "planner" if initial else "replanner"
        instruction = prompts.PLAN if initial else prompts.REPLAN
        if not initial:
            instruction += "\nPrevious remaining plan: " + json.dumps(self.plan, ensure_ascii=False)
            if self.execution_history:
                instruction += "\nLatest executor attempt: " + json.dumps(
                    self.execution_history[-1], ensure_ascii=False)
        current_instruction = instruction
        while True:
            text = self._text_stage(role, current_instruction)
            try:
                plan, completed = validate_plan(text, None if initial else self.plan)
            except (ValueError, TypeError, KeyError) as error:
                # Keep the role, previous plan and evidence rules when repairing
                # syntax; a repair is not a new, context-free planning task.
                self._events.append({"type": "plan_repair", "reason": str(error)})
                current_instruction = prompts.stage_repair(instruction, prompts.BAD_PLAN + " Validation: " + str(error))
                continue
            self.plan = plan
            self.completed_steps.extend(s for s in completed if s not in self.completed_steps)
            return

    def _act(self, request):
        role = "executor" if self.config.harness == "plan_and_execute" else "actor"
        execution_results = []
        while True:
            if self.config.harness == "act_only":
                self._instruction(prompts.ACT_ONLY)
            response = self._complete(role, self._tools)
            calls = response.get("tool_calls") or []
            if self.config.harness == "act_only" and (response.get("content") or "").strip():
                self._reject_calls(calls, "act_only_text")
                self._user(prompts.ACT_ONLY_REPAIR)
                continue
            if not calls:
                self._user(prompts.CONTINUE_ANALYSIS if response['content'].strip()
                           and self.config.harness != 'act_only' else prompts.NO_TOOLS)
                continue
            if response.get("finish_reason") == "length":
                self._reject_calls(calls, "truncated_reply")
                self._user("The reply was truncated. Submit a complete tool reply.")
                continue
            advances = [i for i, call in enumerate(calls) if call["name"] == "advance"]
            if advances and len(calls) != 1:
                self._reject_calls(calls, "advance_requires_separate_reply")
                self._user(prompts.ADVANCE_SEPARATE)
                continue
            if any(not isinstance(call.get("arguments"), dict) for call in calls):
                self._reject_calls(calls, "arguments_must_be_JSON_objects")
                continue
            for call in calls:
                tool_call = ToolCall(call["name"], deepcopy(call["arguments"]))
                try:
                    result = request.call_tool(tool_call)
                    if not isinstance(result, ToolResult):
                        raise TypeError("invalid tool result")
                    rendered = self._adapter.render_tool_result(tool_call, result)
                except Exception as error:
                    # The tool may have applied its effect before raising. Do
                    # not retry or claim the failed call was never submitted.
                    self._tool_response(call["id"], json.dumps({
                        "status": "execution_unknown", "error_type": type(error).__name__,
                        "message": "Tool execution or result delivery failed; this call will not be replayed.",
                    }))
                    self._stop("tool_execution_error")
                self._tool_response(call["id"], rendered)
                if role == "executor":
                    execution_results.append({"tool": call["name"], "status": result.status,
                                              "result_message_index": len(self.messages) - 1})
                if result.status == "decision_ready":
                    if call["name"] != "advance":
                        self._stop("unexpected_decision_boundary")
                    if role == "executor":
                        self.execution_history.append({
                            "task": self.current_step, "submitted_at": self._game_time,
                            "observed_at": None, "tool_results": execution_results,
                        })
                    return self._turn()


def create_agent(call_llm, *, config=None, model=None):
    return ExperimentAgent(call_llm, config, model=model)
