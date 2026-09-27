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
from sc2bench_env.interface.tools import ToolCall, ToolResult, ToolSpec


class LLMAdapter:
    """Renders platform facts. It does not call a model or keep a session."""

    def __init__(self, prompt_parts: Optional[PromptParts] = None, *, race: str = "terran") -> None:
        self.prompt_parts = prompt_parts if prompt_parts is not None else default_prompt_parts(race)

    def system_prompt(self, race: str = "terran") -> str:
        if self.prompt_parts is None:
            return render_prompt(default_prompt_parts(race))
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
        observation = request.observation.to_dict()
        previous = getattr(request.observation, "text_previous", None)
        user = "[Current Observation]\n" + render_observation_text(observation, previous=previous)
        if request.feedback is not None:
            feedback = request.feedback.to_dict()
            user += "\n\n[Previous Feedback]\n" + render_feedback_text(
                feedback, shown_events=observation.get("recent_events", []))
        if not observation.get("terminated", False):
            user += "\n\n" + DECISION_REQUEST
        return user

    def render_tool_result(self, call: ToolCall, result: ToolResult | Mapping[str, Any]) -> str:
        if isinstance(result, ToolResult):
            if result.status == "ok":
                return render_tool_result(call.name, result.data)
            if result.status == "staged":
                data = result.data
                return (
                    f"status: staged\n"
                    f"tool: {data.get('tool')}\n"
                    f"staged_position: {data.get('staged_position')}\n"
                    f"pending_action_count: {data.get('pending_action_count')}\n"
                    "The action is stored for this decision. It is not paid, started or completed."
                )
            if result.status == "decision_ready":
                data = result.data
                return (
                    f"status: decision_ready\n"
                    f"action_count: {data.get('action_count')}\n"
                    f"advance_seconds: {data.get('advance_seconds')}"
                )
            error = result.data.get("error") if isinstance(result.data, Mapping) else {}
            if not isinstance(error, Mapping):
                error = {}
            return (
                f"status: rejected\n"
                f"code: {error.get('code')}\n"
                f"message: {error.get('message')}"
            )
        return render_tool_result(call.name, result)
