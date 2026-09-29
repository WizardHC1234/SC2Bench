"""Public agent turn types. Runners construct these; they do not call models."""
from __future__ import annotations


import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional
from uuid import uuid4

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
    """Harness record for one decision. The DecisionBatch stays on the ToolTurn.

    agent_context may include ``agent_calls``: a list of model-call records.
    Each record uses call_id, role, model, status (``ok`` or ``failure``),
    input_tokens, output_tokens and latency_seconds. Tokens stay null when the
    provider omits them. Failures may also set error_type and http_status.
    Messages and the public reply stay in the session, not in these records.
    """

    agent_context: Optional[dict[str, Any]] = None
    call_failures: tuple[dict[str, Any], ...] = ()
    stop_after_call_failures: bool = False


def _optional_token(value: Any) -> Optional[int]:
    if type(value) is int and value >= 0:
        return value
    return None


def _optional_latency(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def normalize_agent_call(value: Any) -> Optional[dict[str, Any]]:
    """Return the public agent_call fields, or None when the record is unusable.

    Missing or non-integer token counts stay null. They are never estimated.
    """
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    if status in {"error", "failed"}:
        status = "failure"
    if status not in {"ok", "failure"}:
        return None
    call_id = value.get("call_id")
    if not isinstance(call_id, str) or not call_id.strip():
        call_id = uuid4().hex
    role = value.get("role")
    if not isinstance(role, str) or not role.strip():
        role = "main"
    model = value.get("model")
    if not isinstance(model, str) or not model:
        model = None
    http_status = value.get("http_status")
    if type(http_status) is not int or not 100 <= http_status <= 599:
        http_status = None
    error_type = value.get("error_type")
    if not isinstance(error_type, str) or not error_type:
        error_type = None
    return {
        "call_id": call_id,
        "role": role,
        "model": model,
        "status": status,
        "input_tokens": _optional_token(value.get("input_tokens")),
        "output_tokens": _optional_token(value.get("output_tokens")),
        "latency_seconds": _optional_latency(value.get("latency_seconds")),
        "error_type": error_type,
        "http_status": http_status,
    }
