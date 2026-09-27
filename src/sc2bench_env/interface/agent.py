"""Public agent turn types. Runners construct these; they do not call models."""
from __future__ import annotations


from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from sc2bench_env.interface.feedback import Feedback
from sc2bench_env.interface.observations import Observation
from sc2bench_env.interface.tools import ToolCall, ToolResult, ToolSpec

_STOP_REASONS = frozenset({
    "agent_stopped",
    "invalid_decision_limit",
    "tool_round_limit",
})


@dataclass(frozen=True)
class AgentInput:
    observation: Observation
    feedback: Optional[Feedback]
    tool_specs: tuple[ToolSpec, ...] = ()
    call_tool: Callable[[ToolCall], ToolResult] = lambda _call: ToolResult(
        status="rejected",
        data={"error": {"code": "unknown_tool", "message": "tools are not available"}},
    )


class AgentStopped(RuntimeError):
    """An external Agent's deliberate stop, distinct from an unexpected error."""

    def __init__(self, end_reason: str = "agent_stopped") -> None:
        if end_reason not in _STOP_REASONS:
            raise ValueError("Unsupported Agent stop reason")
        super().__init__(end_reason)
        self.end_reason = end_reason


@dataclass(frozen=True)
class AgentTurn:
    """Harness record for one decision. The DecisionBatch stays on the ToolTurn."""

    agent_context: Optional[dict[str, Any]] = None
    call_failures: tuple[dict[str, Any], ...] = ()
    stop_after_call_failures: bool = False
