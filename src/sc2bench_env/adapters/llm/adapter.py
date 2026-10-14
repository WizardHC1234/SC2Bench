"""Turn structured platform input into supplier-neutral LLM text."""
from __future__ import annotations


from typing import Any, Mapping, Optional, Sequence

from sc2bench_env.adapters.llm.tool_results import render_tool_result
from sc2bench_env.interface.agent import AgentInput
from sc2bench_env.interface.observation_text import render_feedback_text
from sc2bench_env.interface.observations import render_observation_text
from sc2bench_env.interface.platform_prompt import (
    PromptParts,
    default_prompt_parts,
    render_prompt,
)
from sc2bench_env.interface.platform_rules import DECISION_REQUEST
from sc2bench_env.interface.races import require_supported_own_race
from sc2bench_env.interface.tools import ToolCall, ToolResult, ToolSpec


def _compact_value(value: Any) -> str:
    if isinstance(value, Mapping):
        return ", ".join(f"{name} {item}" for name, item in value.items()) or "none"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return ", ".join(str(item) for item in value) or "none"
    return str(value)


def _action_result_text(status: str, data: Mapping[str, Any]) -> str:
    tool = str(data.get("action") or "action")
    arguments = data.get("request") if isinstance(data.get("request"), Mapping) else {}
    effect = data.get("effect") if isinstance(data.get("effect"), Mapping) else {}
    target = arguments.get("target")
    count = arguments.get("count")
    if tool == "cancel":
        target = f"{arguments.get('target_action')} {target}"
    elif tool == "morph_townhall":
        target = f"{target} -> {arguments.get('to')}"
    lines = [f"Status: {status}"]
    added = effect.get("added")
    before = effect.get("unfinished_before")
    after = effect.get("unfinished_after")
    if added is not None:
        change = f"added {added}"
        if before is not None:
            change += f"; unfinished before {before}"
        if after is not None:
            change += f"; unfinished after staging {after}"
        lines.append(f"Change: {change}")
    elif effect.get("removed_unstarted") is not None:
        change = f"removed {effect['removed_unstarted']} matching unstarted work"
        if effect.get("remaining_unstarted") is not None:
            change += f"; matching unstarted remaining {effect['remaining_unstarted']}"
        if effect.get("protected_started_or_paid"):
            change += f"; started or paid retained {effect['protected_started_or_paid']}"
        if effect.get("unremoved_count"):
            change += f"; requested amount unavailable {effect['unremoved_count']}"
        lines.append(f"Change: {change}")
    elif effect.get("matched_unstarted") is not None:
        lines.append(f"Change: removed {effect['matched_unstarted']} matching unstarted work")
    elif status == "staged":
        subject = " ".join(str(item) for item in (tool, target) if item not in {None, ""})
        if type(count) is int:
            subject += f" x{count}"
        lines.append(f"Change: staged {subject}".rstrip())
    if effect.get("group") is not None:
        lines.append(f"Group: {effect['group']}")
    if isinstance(effect, Mapping):
        for key in ("requested", "available", "missing", "reinforcement", "blocked"):
            value = effect.get(key)
            if value is not None:
                lines.append(f"{key.title()}: {_compact_value(value)}")
    available = effect.get("available_after_staging")
    if isinstance(available, Mapping):
        lines.append(f"Available after staging: {_compact_value(available)}")
    if effect.get("reason") is not None:
        lines.append(f"Reason: {effect['reason']}")
    error = data.get("error")
    if (
        effect.get("reason") is None
        and isinstance(error, Mapping)
        and error.get("message") is not None
    ):
        lines.append(f"Reason: {error['message']}")
    execution = data.get("execution")
    execution_text = {
        "not_started_until_advance": "begins after advance",
        "transfer_pending": "transfer pending",
        "not_staged": "not staged",
        "no_change": "no change",
    }.get(str(execution), str(execution) if execution else None)
    if execution_text:
        lines.append(f"Execution: {execution_text}")
    normalizations = data.get("normalizations")
    if isinstance(normalizations, Sequence) and not isinstance(normalizations, (str, bytes)):
        for row in normalizations:
            if isinstance(row, Mapping):
                lines.append(
                    f"Normalized: {row.get('original')} -> {row.get('canonical')}"
                )
    return "\n".join(lines)


class LLMAdapter:
    """Renders platform facts. It does not call a model or keep a session."""

    def __init__(self, prompt_parts: Optional[PromptParts] = None, *, race: str = "terran") -> None:
        require_supported_own_race(race)
        self.race = race
        self.prompt_parts = prompt_parts if prompt_parts is not None else default_prompt_parts(self.race)

    def system_prompt(self) -> str:
        return render_prompt(self.prompt_parts)

    def render_tools(self, specs: Sequence[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": dict(spec.parameters),
                },
            }
            for spec in specs
        ]

    def render_input(self, request: AgentInput) -> str:
        from sc2bench_env.adapters.llm.observation import platform_messages

        observation = request.observation.to_dict()
        previous = getattr(request.observation, "text_previous", None)
        feedback = request.feedback.to_dict() if request.feedback is not None else None
        return platform_messages("", observation, feedback, previous=previous)[1]["content"]

    def render_tool_result(self, call: ToolCall, result: ToolResult | Mapping[str, Any]) -> str:
        if isinstance(result, ToolResult):
            if result.status == "ok":
                return render_tool_result(call.name, result.data)
            if result.status == "staged":
                return _action_result_text("staged", result.data)
            if result.status == "no_op":
                return _action_result_text("no_op", result.data)
            if result.status == "decision_ready":
                data = result.data
                request = data.get("request") if isinstance(data.get("request"), Mapping) else {}
                effect = data.get("effect") if isinstance(data.get("effect"), Mapping) else {}
                return (
                    f"Status: decision_ready\n"
                    f"Submitted actions: {effect.get('submitted_actions')}\n"
                    f"Game advance: {request.get('seconds')} seconds"
                )
            error = result.data.get("error") if isinstance(result.data, Mapping) else {}
            if not isinstance(error, Mapping):
                error = {}
            if result.data.get("action"):
                return _action_result_text("rejected", result.data)
            return (
                f"Status: rejected\n"
                f"Reason: {error.get('message')}\n"
                f"Code: {error.get('code')}"
            )
        return render_tool_result(call.name, result)
