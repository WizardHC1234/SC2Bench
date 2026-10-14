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
    KNOWLEDGE_TOOLS,
    READ_TOOLS,
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
        preview_actions: Optional[
            Callable[[Sequence[GameAction]], Mapping[str, Any]]
        ] = None,
    ) -> None:
        self.turn_id = uuid4().hex
        self.race = race
        self._observation = observation
        self._game_time = float(game_time_seconds)
        self._decision_index = int(decision_index)
        self._record = record
        self._preview_actions = preview_actions
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

    def call_many(self, tool_calls: Sequence[ToolCall]) -> tuple[ToolResult, ...]:
        """Apply calls in order using the same semantics as repeated call()."""
        results: list[ToolResult] = []
        for tool_call in tool_calls:
            result = self.call(tool_call)
            results.append(result)
        return tuple(results)

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
            # Parse the public call itself so GameAction retains the public tool
            # name while its internal action is normalized for the runtime.
            action = parse_game_action(tool_call.to_dict(), race=self.race)
        except ActionValidationError as exc:
            return self._reject("invalid_arguments", str(exc), tool_call)

        preview: Mapping[str, Any] = {}
        if self._preview_actions is not None:
            preview = self._preview_actions((*self._staged, action))
        receipt = preview.get("receipt") if isinstance(preview, Mapping) else None
        if not isinstance(receipt, Mapping):
            receipt = {
                "name": action.action,
                "arguments": action.to_tool_call().get("arguments", {}),
                "result": "accepted",
            }
        outcome = str(receipt.get("result") or "accepted")
        data = self._action_result_data(tool_call, action, receipt, preview, changes)
        if outcome == "rejected":
            reason = str(receipt.get("reason") or "action rejected")
            data["execution"] = "not_staged"
            result = ToolResult(
                status="rejected",
                data={
                    **data,
                    "error": {"code": "action_rejected", "message": reason},
                },
            )
            self._record_call("action", tool_call, result)
            return result
        no_op = outcome in {"idempotent_noop", "ignored_duplicate_action_id"}
        if (
            action.action == "cancel"
            and outcome == "accepted"
            and receipt.get("reason") == "cleared_waiting=0"
        ):
            no_op = True
        if no_op:
            data["execution"] = "no_change"
            result = ToolResult(status="no_op", data=data)
            self._record_call("action", tool_call, result)
            return result

        self._normalizations.extend(changes)
        self._staged.append(action)
        self._staged_calls.append(tool_call.to_dict())
        result = ToolResult(status="staged", data=data)
        self._record_call("action", tool_call, result)
        return result

    def _action_result_data(
        self,
        tool_call: ToolCall,
        action: GameAction,
        receipt: Mapping[str, Any],
        preview: Mapping[str, Any],
        normalizations: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        arguments = dict(tool_call.arguments)
        if "target" in arguments and action.target is not None:
            arguments["target"] = action.target
        if tool_call.name == "scout" and action.route is not None:
            from sc2bench_env.interface.actions import route_payload
            arguments["route"] = route_payload(action.route)
        effect: dict[str, Any] = {}
        data: dict[str, Any] = {
            "action": tool_call.name,
            "request": dict(arguments),
            "effect": effect,
            "execution": "not_started_until_advance",
        }
        receipt_details = receipt.get("details")
        has_cancel_effect = (
            action.action == "cancel"
            and isinstance(receipt_details, Mapping)
            and receipt_details.get("removed_unstarted") is not None
        )
        if receipt.get("reason") is not None and not has_cancel_effect:
            effect["reason"] = receipt.get("reason")
        if isinstance(receipt_details, Mapping):
            effect.update(dict(receipt_details))
            if effect.get("execution") is not None:
                data["execution"] = effect.pop("execution")
        for key in ("group", "destination", "sources"):
            value = (receipt.get("arguments") or {}).get(key) if isinstance(
                receipt.get("arguments"), Mapping
            ) else None
            if value is not None:
                effect[key] = value
        if normalizations:
            data["normalizations"] = [dict(row) for row in normalizations]

        before = preview.get("before_priority") if isinstance(preview, Mapping) else None
        after = preview.get("after_priority") if isinstance(preview, Mapping) else None
        target_action = action.target_action if action.action == "cancel" else action.action
        target = action.target or ""
        if isinstance(before, Sequence) and isinstance(after, Sequence) and target_action in {
            "build", "train", "research", "morph_townhall",
        }:
            def total(rows: Sequence[Any]) -> int:
                return sum(
                    int(row.get("remaining") or 0)
                    for row in rows if isinstance(row, Mapping)
                    and row.get("action") == target_action
                    and row.get("target") == target
                )
            effect["unfinished_before"] = total(before)
            effect["unfinished_after"] = total(after)
            if (
                receipt.get("result", "accepted") == "accepted"
                and action.action in {"build", "train", "research", "morph_townhall"}
            ):
                effect["added"] = int(action.count or 1)
        if (
            action.action == "cancel"
            and effect.get("removed_unstarted") is None
            and isinstance(receipt.get("reason"), str)
        ):
            prefix = "cleared_waiting="
            if str(receipt["reason"]).startswith(prefix):
                raw = str(receipt["reason"])[len(prefix):]
                if raw.isdigit():
                    effect["matched_unstarted"] = int(raw)
        if action.action == "combat" and action.units and action.source in {None, "group_0"}:
            free = self._observation.get("own_forces", {}).get("free", {})
            remaining = {
                str(name): int(count) for name, count in dict(free or {}).items()
                if type(count) is int and count > 0
            }
            combat_actions = self._staged
            if receipt.get("result", "accepted") == "accepted":
                combat_actions = (*combat_actions, action)
            for staged in combat_actions:
                if staged.action != "combat" or not staged.units:
                    continue
                for name, count in staged.units.items():
                    remaining[name] = max(0, int(remaining.get(name, 0)) - int(count))
            effect["available_after_staging"] = {
                name: count for name, count in remaining.items() if count > 0
            }
        return data

    def _advance(self, tool_call: ToolCall) -> ToolResult:
        try:
            advance = parse_advance_action(tool_call.to_dict(), race=self.race)
        except ActionValidationError as exc:
            return self._reject("invalid_arguments", str(exc), tool_call)
        raw = tuple(self._staged_calls) + (tool_call.to_dict(),)
        self._batch = DecisionBatch(
            actions=tuple(self._staged), advance=advance, raw=raw,
            normalizations=tuple(self._normalizations),
            receipts_delivered=True,
        )
        self._closed = True
        result = ToolResult(
            status="decision_ready",
            decision_boundary=True,
            data={
                "action": "advance",
                "request": {"seconds": advance.seconds},
                "effect": {"submitted_actions": len(self._staged)},
                "execution": "decision_boundary",
            },
        )
        self._record_call("action", tool_call, result)
        return result

    def _reject(self, code: str, message: str, tool_call: Optional[ToolCall]) -> ToolResult:
        result = rejected_result(code, message)
        if tool_call is not None:
            spec = self._specs.get(tool_call.name)
            if spec is not None:
                kind = spec.kind
            elif tool_call.name in KNOWLEDGE_TOOLS:
                kind = "knowledge"
            elif tool_call.name in READ_TOOLS:
                kind = "read"
            elif tool_call.name in known_tool_names():
                kind = "action"
            else:
                kind = "unknown"
            if kind == "action":
                result = ToolResult(
                    status="rejected",
                    data={
                        "action": tool_call.name,
                        "request": dict(tool_call.arguments),
                        "effect": {},
                        "execution": "not_staged",
                        "error": {"code": code, "message": message},
                    },
                )
            self._record_call(kind, tool_call, result)
        return result

    def _record_call(self, kind: str, tool_call: ToolCall, result: ToolResult) -> None:
        if self._record is None or not tool_call.name:
            return
        self._record(
            # Kind describes the tool family. Rejection is a result status and
            # must not erase whether this was an action, read or knowledge call.
            kind=kind,
            call=tool_call.to_dict(),
            result=result,
            game_time_seconds=self._game_time,
            decision_index=self._decision_index,
            turn_id=self.turn_id,
        )
