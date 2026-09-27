"""One decision's tool calls: query immediately, stage actions, close on advance."""
from __future__ import annotations


from typing import Any, Callable, Mapping, Optional, Sequence
from uuid import uuid4

from sc2bench_env.interface.actions import (
    ActionValidationError,
    DecisionBatch,
    GameAction,
    parse_advance_action,
    parse_game_action,
)
from sc2bench_env.interface.tools import (
    ToolCall,
    ToolResult,
    ToolSpec,
    known_tool_names,
    rejected_result,
    validate_tool_arguments,
)


class ToolTurnError(RuntimeError):
    """Agent protocol failure for this decision. Not a game-action rejection."""

    def __init__(self, code: str) -> None:
        if code not in {"missing_advance", "turn_invalid", "already_consumed", "turn_in_progress"}:
            raise ValueError(f"unsupported tool-turn error {code}")
        super().__init__(code)
        self.code = code


class ToolTurn:
    """Stages one decision. DecisionBatch stays here until the runner calls finish()."""

    def __init__(
        self,
        *,
        specs: Sequence[ToolSpec],
        race: str,
        observation: Mapping[str, Any],
        game_time_seconds: float,
        decision_index: int,
        record: Optional[Callable[..., None]] = None,
    ) -> None:
        self.turn_id = uuid4().hex
        self.race = race
        self._observation = observation
        self._game_time = float(game_time_seconds)
        self._decision_index = int(decision_index)
        self._record = record
        self._specs = {spec.name: spec for spec in specs}
        self._staged: list[GameAction] = []
        self._staged_calls: list[dict[str, Any]] = []
        self._normalizations: list[dict[str, Any]] = []
        self._batch: Optional[DecisionBatch] = None
        self._closed = False
        self._invalid = False
        self._consumed = False
        self._retired = False

    @property
    def active(self) -> bool:
        return not self._retired and not self._consumed

    def call(self, tool_call: ToolCall) -> ToolResult:
        if not isinstance(tool_call, ToolCall):
            return self._reject("invalid_arguments", "tool call must be a ToolCall", None)
        if self._retired or self._consumed:
            return self._reject("stale_turn", "this tool turn is no longer active", tool_call)
        if self._closed:
            self._invalid = True
            self._batch = None
            return self._reject("turn_closed", "advance already closed this decision", tool_call)
        spec = self._specs.get(tool_call.name)
        if spec is None:
            if tool_call.name in known_tool_names():
                return self._reject(
                    "unavailable_for_race",
                    f"{tool_call.name} is not available for {self.race}",
                    tool_call,
                )
            return self._reject("unknown_tool", f"unknown tool {tool_call.name}", tool_call)
        argument_error = validate_tool_arguments(spec, tool_call.arguments)
        if argument_error:
            return self._reject("invalid_arguments", argument_error, tool_call)
        if spec.kind in {"knowledge", "read"}:
            return self._query(spec, tool_call)
        if tool_call.name == "advance":
            return self._advance(tool_call)
        return self._stage(tool_call)

    def finish(self) -> DecisionBatch:
        if self._consumed:
            raise ToolTurnError("already_consumed")
        if self._invalid or (self._closed and self._batch is None):
            self._consumed = True
            raise ToolTurnError("turn_invalid")
        if self._batch is None:
            self._consumed = True
            raise ToolTurnError("missing_advance")
        self._consumed = True
        batch = self._batch
        self._batch = None
        return batch

    def abort(self) -> None:
        self._staged.clear()
        self._staged_calls.clear()
        self._normalizations.clear()
        self._batch = None
        self._closed = True
        self._retired = True

    def retire(self) -> None:
        self._retired = True
        self._batch = None

    def _query(self, spec: ToolSpec, tool_call: ToolCall) -> ToolResult:
        from sc2bench_env.runtime.query_tools import run_query

        payload = run_query(
            tool_call.name, tool_call.arguments,
            observation=self._observation, race=self.race,
        )
        if not isinstance(payload, Mapping):
            payload = {"value": payload}
        error = payload.get("error")
        if isinstance(error, str) and error.startswith("unknown_tool"):
            return self._reject("unknown_tool", error, tool_call)
        if isinstance(error, str) and (
            "must be" in error or error.startswith("race must be")
        ):
            return self._reject("invalid_arguments", error, tool_call)
        result = ToolResult(status="ok", data=dict(payload))
        self._record_call(spec.kind, tool_call, result)
        return result

    def _stage(self, tool_call: ToolCall) -> ToolResult:
        try:
            from sc2bench_env.catalog.aliases import normalize_target_aliases
            from sc2bench_env.interface.tools import parse_tool_call

            parsed = parse_tool_call(tool_call.to_dict())
            _internal, changes = normalize_target_aliases(
                parsed.to_internal_entry(), index=len(self._staged), race=self.race,
            )
            self._normalizations.extend(changes)
            action = parse_game_action(tool_call.to_dict(), race=self.race)
        except ActionValidationError as exc:
            return self._reject("invalid_arguments", str(exc), tool_call)
        self._staged.append(action)
        self._staged_calls.append(tool_call.to_dict())
        result = ToolResult(
            status="staged",
            data={
                "tool": action.action,
                "staged_position": len(self._staged),
                "pending_action_count": len(self._staged),
            },
        )
        self._record_call("action", tool_call, result)
        return result

    def _advance(self, tool_call: ToolCall) -> ToolResult:
        try:
            advance = parse_advance_action(tool_call.to_dict(), race=self.race)
        except ActionValidationError as exc:
            return self._reject("invalid_arguments", str(exc), tool_call)
        raw = tuple(self._staged_calls) + (tool_call.to_dict(),)
        self._batch = DecisionBatch(
            actions=tuple(self._staged), advance=advance, raw=raw,
            normalizations=tuple(self._normalizations),
        )
        self._closed = True
        result = ToolResult(
            status="decision_ready",
            decision_boundary=True,
            data={
                "action_count": len(self._staged),
                "advance_seconds": advance.seconds,
                "staged_calls": list(self._staged_calls),
            },
        )
        self._record_call("action", tool_call, result)
        return result

    def _reject(self, code: str, message: str, tool_call: Optional[ToolCall]) -> ToolResult:
        result = rejected_result(code, message)
        if tool_call is not None:
            self._record_call("rejected", tool_call, result)
        return result

    def _record_call(self, kind: str, tool_call: ToolCall, result: ToolResult) -> None:
        if self._record is None or not tool_call.name:
            return
        self._record(
            kind=kind if result.status != "rejected" else "rejected",
            call=tool_call.to_dict(),
            result=result,
            game_time_seconds=self._game_time,
            decision_index=self._decision_index,
            turn_id=self.turn_id,
        )
