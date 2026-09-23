"""Agent-facing action data structures and validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

from sc2bench_env.interface.action_catalog import COMBAT_STYLES, known_target_names
from sc2bench_env.interface.target_aliases import normalize_target_aliases
from sc2bench_env.interface.races import require_supported_own_race
from sc2bench_env.interface.observations import WORKER_UNIT_NAMES
from sc2bench_env.interface.scouting import ScoutRoute, route_payload
from sc2bench_env.interface.decision_rules import (
    STRUCTURE_ID_PATTERN,
    ZONE_PATTERN,
    DecisionSchemaError,
    validate_batch_shape,
    validate_entry_fields,
)
from sc2bench_env.interface.tools import parse_normalized_tool_call

_ZONE_TARGET_RE = re.compile(ZONE_PATTERN)
_STRUCTURE_ID_RE = re.compile(STRUCTURE_ID_PATTERN)

GAME_ACTIONS = frozenset(
    {
        "build",
        "train",
        "research",
        "cancel",
        "scan",
        "call_mule",
        "chrono_boost",
        "inject_larva",
        "spawn_creep_tumor",
        "scout",
        "upgrade",
        "combat",
        "retreat",
    }
)


class ActionValidationError(ValueError):
    """Raised when a decision payload fails validation."""


@dataclass(frozen=True)
class AdvanceAction:
    """Trailing command: run the game for a positive number of seconds."""

    seconds: float

    @property
    def action(self) -> str:
        return "advance"

    def to_dict(self) -> dict[str, Any]:
        return {"action": "advance", "seconds": self.seconds}

    def to_tool_call(self) -> dict[str, Any]:
        return {"name": "advance", "arguments": {"seconds": self.seconds}}


@dataclass(frozen=True)
class GameAction:
    """One high-level command before the trailing advance."""

    action: str
    target: Optional[str] = None
    count: Optional[int] = None
    target_action: Optional[str] = None
    to: Optional[str] = None
    route: Optional[ScoutRoute] = None
    units: Optional[Dict[str, int]] = None
    style: Optional[str] = None
    action_id: Optional[str] = None
    group: Optional[str] = None

    def identity(self) -> tuple[str, str]:
        return (self.action, self.target or "")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"action": self.action}
        if self.target is not None:
            payload["target"] = self.target
        # Model Schema forbids count on build/research/scan; keep it internal only.
        if self.count is not None and self.action == "train":
            payload["count"] = self.count
        if self.target_action is not None:
            payload["target_action"] = self.target_action
        if self.to is not None:
            payload["to"] = self.to
        if self.route is not None:
            payload["route"] = route_payload(self.route)
        if self.units is not None:
            payload["units"] = dict(self.units)
        if self.style is not None:
            payload["style"] = self.style
        if self.action_id is not None:
            payload["action_id"] = self.action_id
        if self.group is not None:
            payload["group"] = self.group
        return payload

    def label(self) -> str:
        if self.action == "train":
            return f"train {self.target} {self.count}"
        if self.action == "cancel":
            return f"cancel {self.target_action} {self.target}"
        if self.action in {"call_mule", "chrono_boost", "inject_larva", "spawn_creep_tumor"}:
            return self.action
        if self.action == "combat":
            units = self.units or {}
            composition = " ".join(f"{name} {count}" for name, count in sorted(units.items()))
            return f"{self.style} {self.target} with {composition}".strip()
        if self.target:
            return f"{self.action} {self.target}"
        return self.action

    def to_tool_call(self) -> dict[str, Any]:
        payload = self.to_dict()
        name = payload.pop("action")
        return {"name": name, "arguments": payload}


DecisionAction = Union[GameAction, AdvanceAction]


@dataclass(frozen=True)
class DecisionBatch:
    """Validated decision: ordered game actions + mandatory trailing advance."""

    actions: tuple[GameAction, ...]
    advance: AdvanceAction
    raw: tuple[dict[str, Any], ...] = ()
    normalizations: tuple[dict[str, Any], ...] = ()

    def game_actions(self) -> List[GameAction]:
        return list(self.actions)

    def to_dicts(self) -> List[dict[str, Any]]:
        return [action.to_tool_call() for action in self.actions] + [self.advance.to_tool_call()]


def _require_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActionValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _as_validation_error(exc: DecisionSchemaError) -> ActionValidationError:
    return ActionValidationError(str(exc))


def _validate_race(race: str) -> None:
    try:
        require_supported_own_race(race)
    except ValueError as exc:
        raise ActionValidationError(str(exc)) from exc


def _internal_entry(raw: Mapping[str, Any], *, index: int = 0, race: str = "terran") -> dict[str, Any]:
    try:
        call = parse_normalized_tool_call(raw, index=index)
        internal = call.to_internal_entry()
    except ValueError as exc:
        raise ActionValidationError(str(exc)) from exc
    internal, _ = normalize_target_aliases(internal, index=index, race=race)
    return internal


def parse_advance_action(raw: Mapping[str, Any], *, race: str = "terran") -> AdvanceAction:
    try:
        verb = validate_entry_fields(raw, index=0, race=race)
    except DecisionSchemaError as exc:
        raise _as_validation_error(exc) from exc
    if verb != "advance":
        raise ActionValidationError("trailing action must be advance")
    internal = _internal_entry(raw, race=race)
    seconds = internal.get("seconds")
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
        raise ActionValidationError("advance.seconds must be a positive number")
    return AdvanceAction(seconds=float(seconds))


def parse_game_action(raw: Mapping[str, Any], *, race: str = "terran") -> GameAction:
    if not isinstance(raw, Mapping):
        raise ActionValidationError("action must be a mapping")
    _validate_race(race)
    raw = _internal_entry(raw, race=race)
    try:
        rebuilt = {
            "name": raw["action"],
            "arguments": {key: value for key, value in raw.items() if key != "action"},
        }
        action = validate_entry_fields(rebuilt, index=0, race=race)
    except DecisionSchemaError as exc:
        raise _as_validation_error(exc) from exc
    if action in {"advance", "wait"}:
        raise ActionValidationError("advance must appear only as the trailing decision entry")
    if action not in GAME_ACTIONS:
        raise ActionValidationError(
            f"unsupported action {action!r}; allowed={sorted(GAME_ACTIONS | {'advance'})}"
        )

    if action == "build":
        target = _require_str(raw.get("target"), "target").lower()
        if target in {"orbital_command", "planetary_fortress"}:
            raise ActionValidationError(
                f"use upgrade with a structures[].id to morph {target}; "
                'example: {"name":"upgrade","arguments":{"target":"cc_0","to":"orbital_command"}}'
            )
        if target not in known_target_names("build", race=race):
            raise ActionValidationError(
                f"unsupported build target {target!r}; allowed={list(known_target_names('build', race=race))}"
            )
        return GameAction(action="build", target=target, count=1)

    if action == "train":
        target = _require_str(raw.get("target"), "target").lower()
        count = raw.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ActionValidationError("train count must be a positive integer")
        if target not in known_target_names("train", race=race):
            raise ActionValidationError(
                f"unsupported train target {target!r}; allowed={list(known_target_names('train', race=race))}"
            )
        return GameAction(action="train", target=target, count=count)

    if action == "research":
        target = _require_str(raw.get("target"), "target").lower()
        if target not in known_target_names("research", race=race):
            raise ActionValidationError(
                f"unsupported research target {target!r}; "
                f"allowed={list(known_target_names('research', race=race))}"
            )
        return GameAction(action="research", target=target, count=1)

    if action == "cancel":
        target_action = _require_str(raw.get("target_action"), "target_action").lower()
        if target_action not in {"build", "train", "research"}:
            raise ActionValidationError("cancel.target_action must be build, train, or research")
        target = _require_str(raw.get("target"), "target").lower()
        return GameAction(
            action="cancel",
            target=target,
            target_action=target_action,
        )

    if action == "scan":
        if not known_target_names("scan", race=race):
            raise ActionValidationError("scan is not available for this race")
        target = _require_str(raw.get("target"), "target").lower()
        if not _ZONE_TARGET_RE.match(target):
            raise ActionValidationError("scan target must be a stable zone_id")
        return GameAction(action="scan", target=target, count=1)

    if action in {"call_mule", "chrono_boost", "inject_larva", "spawn_creep_tumor"}:
        if not known_target_names(action, race=race):
            raise ActionValidationError(f"{action} is not available for this race")
        return GameAction(action=action)

    if action == "scout":
        route = raw.get("route")
        if route == "all":
            return GameAction(action="scout", route="all")
        if not isinstance(route, Sequence) or isinstance(route, (str, bytes)) or not route:
            raise ActionValidationError("scout.route must be a non-empty sequence of zone_ids")
        normalized = []
        for item in route:
            zone = _require_str(item, "route item").lower()
            if not _ZONE_TARGET_RE.match(zone):
                raise ActionValidationError(f"invalid scout zone_id {zone!r}")
            normalized.append(zone)
        return GameAction(action="scout", route=tuple(normalized))

    if action == "upgrade":
        target = _require_str(raw.get("target"), "target")
        if not _STRUCTURE_ID_RE.match(target):
            raise ActionValidationError(
                "upgrade.target must be a structures[].id such as cc_0"
            )
        to = _require_str(raw.get("to"), "to").lower()
        if to not in known_target_names("upgrade", race=race):
            raise ActionValidationError(
                f"upgrade.to must be one of {list(known_target_names('upgrade', race=race))}"
            )
        return GameAction(action="upgrade", target=target, to=to)

    if action == "combat":
        style = _require_str(raw.get("style"), "style").lower()
        if style not in COMBAT_STYLES:
            raise ActionValidationError(
                f"combat.style must be one of {list(COMBAT_STYLES)}"
            )
        target = _require_str(raw.get("target"), "target").lower()
        if not _ZONE_TARGET_RE.match(target):
            raise ActionValidationError("combat.target must be a stable zone_id")
        units_raw = raw.get("units")
        if raw.get("group") is not None:
            return GameAction(action="combat", target=target, style=style, group=raw["group"])
        if not isinstance(units_raw, Mapping) or not units_raw:
            raise ActionValidationError("combat.units must be a non-empty mapping")
        train_names = set(known_target_names("train", race=race)) - WORKER_UNIT_NAMES
        normalized_units: Dict[str, int] = {}
        for unit_name, count in units_raw.items():
            name = _require_str(unit_name, "units key").lower()
            if name not in train_names:
                raise ActionValidationError(
                    f"combat.units key {name!r} must be an army train target"
                )
            if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                raise ActionValidationError(
                    f"combat.units[{name!r}] must be a positive integer"
                )
            normalized_units[name] = int(count)
        return GameAction(
            action="combat",
            target=target,
            style=style,
            units=normalized_units,
        )

    if action == "retreat":
        return GameAction(action="retreat", group=raw["group"])

    raise ActionValidationError(f"unsupported action {action!r}")


def parse_decision(raw_actions: Sequence[Mapping[str, Any]] | None, *, race: str = "terran") -> DecisionBatch:
    """Parse one Agent decision of NormalizedToolCall objects. Requires a trailing advance."""
    return parse_tool_decision(raw_actions, race=race)


def parse_tool_decision(raw_actions: Sequence[Mapping[str, Any]] | None, *, race: str = "terran") -> DecisionBatch:
    """Parse a NormalizedToolCall batch. Rejects the legacy flat action format."""
    _validate_race(race)
    normalized = raw_actions
    normalizations = []
    if isinstance(raw_actions, Sequence) and not isinstance(raw_actions, (str, bytes)):
        normalized = []
        for index, entry in enumerate(raw_actions):
            if isinstance(entry, Mapping):
                try:
                    call = parse_normalized_tool_call(entry, index=index)
                    internal = call.to_internal_entry()
                except ValueError as exc:
                    raise ActionValidationError(str(exc)) from exc
                internal, changes = normalize_target_aliases(internal, index=index, race=race)
                normalizations.extend(changes)
                entry = {
                    "name": internal["action"],
                    "arguments": {key: value for key, value in internal.items() if key != "action"},
                }
            normalized.append(entry)
    try:
        validate_batch_shape(normalized, race=race)
    except DecisionSchemaError as exc:
        raise _as_validation_error(exc) from exc

    assert raw_actions is not None
    raw_dicts: List[dict[str, Any]] = [dict(item) for item in raw_actions]
    game_actions = tuple(parse_game_action(entry, race=race) for entry in normalized[:-1])
    advance = parse_advance_action(normalized[-1], race=race)
    return DecisionBatch(actions=game_actions, advance=advance, raw=tuple(raw_dicts),
                         normalizations=tuple(normalizations))


def attach_retry_ids(
    batch: DecisionBatch,
    retry_ids: Sequence[Optional[str]] | None,
) -> DecisionBatch:
    """Attach harness-owned retry ids to parsed game actions (not the trailing advance)."""
    if not retry_ids:
        return batch
    if len(retry_ids) != len(batch.actions):
        raise ActionValidationError(
            f"retry_ids length {len(retry_ids)} must match game action count {len(batch.actions)}"
        )
    attached: List[GameAction] = []
    for action, retry_id in zip(batch.actions, retry_ids):
        if retry_id is None:
            attached.append(action)
            continue
        attached.append(
            GameAction(
                action=action.action,
                target=action.target,
                count=action.count,
                target_action=action.target_action,
                to=action.to,
                route=action.route,
                units=action.units,
                style=action.style,
                action_id=_require_str(retry_id, "retry_id"),
                group=action.group,
            )
        )
    return DecisionBatch(actions=tuple(attached), advance=batch.advance, raw=batch.raw,
                         normalizations=batch.normalizations)


# Back-compat aliases used while older modules are migrated.
Action = GameAction


def parse_action(raw: Mapping[str, Any] | GameAction, *, race: str = "terran") -> GameAction:
    _validate_race(race)
    if isinstance(raw, GameAction):
        return raw
    return parse_game_action(raw, race=race)


def parse_actions(raw_actions: Sequence[Mapping[str, Any] | GameAction] | None, *, race: str = "terran") -> List[GameAction]:
    """Parse game actions only (no trailing advance). Prefer parse_decision for Agent input."""
    _validate_race(race)
    if raw_actions is None:
        return []
    if not isinstance(raw_actions, Sequence) or isinstance(raw_actions, (str, bytes)):
        raise ActionValidationError("actions must be a sequence")
    return [
        item if isinstance(item, GameAction) else parse_game_action(item, race=race)
        for item in raw_actions
    ]


def action_batch_to_dicts(actions: Iterable[GameAction] | DecisionBatch) -> List[dict[str, Any]]:
    if isinstance(actions, DecisionBatch):
        return actions.to_dicts()
    return [action.to_dict() for action in actions]
