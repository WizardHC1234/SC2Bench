"""Agent-facing action data structures and validation."""
from __future__ import annotations

from difflib import get_close_matches


import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from sc2bench_env.catalog.aliases import normalize_target_aliases
from sc2bench_env.catalog.registry import dispatchable_unit_names, known_target_names
from sc2bench_env.interface.decision_rules import (
    COMBAT_STYLES,
    STRUCTURE_ID_PATTERN,
    ZONE_PATTERN,
    DecisionSchemaError,
    validate_batch_shape,
    validate_entry_fields,
)
from sc2bench_env.interface.races import require_supported_own_race
from sc2bench_env.interface.tools import parse_tool_call

ScoutRoute = Union[str, Tuple[str, ...]]


def route_payload(route: Optional[ScoutRoute]):
    return route if isinstance(route, str) else list(route or ())


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
        "supply_drop",
        "chrono_boost",
        "inject_larva",
        "spawn_creep_tumor",
        "scout",
        "morph_townhall",
        "combat",
        "merge_groups",
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
    method: Optional[str] = None
    destination: Optional[str] = None
    sources: Optional[Tuple[str, ...]] = None
    source: Optional[str] = None
    public_action: Optional[str] = None

    def identity(self) -> tuple[str, str]:
        return (self.action, self.target or "")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"action": self.action}
        if self.target is not None:
            payload["target"] = self.target
        # Model Schema uses count for train and for a bounded cancellation.
        if self.count is not None and self.action in {"train", "cancel"}:
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
        if self.method is not None:
            payload["method"] = self.method
        if self.destination is not None:
            payload["destination"] = self.destination
        if self.sources is not None:
            payload["sources"] = list(self.sources)
        if self.source is not None:
            payload["source"] = self.source
        return payload

    def label(self) -> str:
        if self.action == "train":
            return f"train {self.target} {self.count}"
        if self.action == "cancel":
            amount = "" if self.count is None else f" {self.count}"
            return f"cancel {self.target_action} {self.target}{amount}"
        if self.action in {"call_mule", "supply_drop", "chrono_boost", "inject_larva", "spawn_creep_tumor"}:
            return self.action
        if self.action == "combat":
            if self.group and self.units:
                composition = " ".join(f"{name} {count}" for name, count in sorted(self.units.items()))
                return f"reinforce {self.group} with {composition}"
            units = self.units or {}
            composition = " ".join(f"{name} {count}" for name, count in sorted(units.items()))
            return f"{self.style} {self.target} with {composition}".strip()
        if self.action == "merge_groups":
            return f"merge {', '.join(self.sources or ())} into {self.destination}"
        if self.target:
            return f"{self.action} {self.target}"
        return self.action

    def to_tool_call(self) -> dict[str, Any]:
        if self.public_action == "dispatch_group":
            return {
                "name": "dispatch_group",
                "arguments": {
                    "source": self.source,
                    "units": dict(self.units or {}),
                    "order": self.style,
                    "target": self.target,
                },
            }
        if self.public_action in {"attack_group", "defend_group"}:
            return {
                "name": self.public_action,
                "arguments": {"group": self.group, "target": self.target},
            }
        if self.public_action == "reinforce_group":
            return {
                "name": "reinforce_group",
                "arguments": {
                    "source": self.source,
                    "destination": self.destination,
                    "units": dict(self.units or {}),
                },
            }
        if self.public_action == "retreat_group":
            arguments = {"group": self.group}
            if self.method is not None:
                arguments["method"] = self.method
            return {"name": "retreat_group", "arguments": arguments}
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
    receipts_delivered: bool = False

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
        call = parse_tool_call(raw, index=index)
        internal = call.to_internal_entry()
    except ValueError as exc:
        raise ActionValidationError(str(exc)) from exc
    internal, _ = normalize_target_aliases(internal, index=index, race=race)
    return internal


def parse_advance_action(raw: Mapping[str, Any], *, race: str = "terran") -> AdvanceAction:
    internal = _internal_entry(raw, race=race)
    try:
        verb = validate_entry_fields(internal, index=0, race=race)
    except DecisionSchemaError as exc:
        raise _as_validation_error(exc) from exc
    if verb != "advance":
        raise ActionValidationError("trailing action must be advance")
    seconds = internal.get("seconds")
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
        raise ActionValidationError("advance.seconds must be a positive number")
    return AdvanceAction(seconds=float(seconds))


def _unsupported_name(kind: str, name: str, choices: Sequence[str]) -> str:
    candidates = get_close_matches(name, list(choices), n=3)
    suffix = f"; closest valid: {', '.join(candidates)}" if candidates else ""
    return f"unsupported {kind} {name!r}{suffix}"


def parse_game_action(raw: Mapping[str, Any], *, race: str = "terran") -> GameAction:
    if not isinstance(raw, Mapping):
        raise ActionValidationError("action must be a mapping")
    _validate_race(race)
    try:
        public_action = parse_tool_call(raw).name
    except ValueError as exc:
        raise ActionValidationError(str(exc)) from exc
    raw = _internal_entry(raw, race=race)
    try:
        action = validate_entry_fields(raw, index=0, race=race)
    except DecisionSchemaError as exc:
        raise _as_validation_error(exc) from exc
    if action in {"advance", "wait"}:
        raise ActionValidationError("advance must appear only as the trailing decision entry")
    if action not in GAME_ACTIONS:
        raise ActionValidationError(_unsupported_name(
            "action", action, sorted(GAME_ACTIONS | {"advance"}),
        ))

    if action == "build":
        target = _require_str(raw.get("target"), "target").lower()
        if target in {"orbital_command", "planetary_fortress"}:
            raise ActionValidationError(
                f"use morph_townhall with a structures[].id to morph {target}; "
                'example: {"name":"morph_townhall","arguments":{"target":"townhall_0","to":"orbital_command"}}'
            )
        if target not in known_target_names("build", race=race):
            raise ActionValidationError(_unsupported_name(
                "build target", target, known_target_names("build", race=race),
            ))
        return GameAction(action="build", target=target, count=1)

    if action == "train":
        target = _require_str(raw.get("target"), "target").lower()
        count = raw.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ActionValidationError("train count must be a positive integer")
        if target not in known_target_names("train", race=race):
            raise ActionValidationError(_unsupported_name(
                "train target", target, known_target_names("train", race=race),
            ))
        return GameAction(action="train", target=target, count=count)

    if action == "research":
        target = _require_str(raw.get("target"), "target").lower()
        if target not in known_target_names("research", race=race):
            raise ActionValidationError(_unsupported_name(
                "research target", target, known_target_names("research", race=race),
            ))
        return GameAction(action="research", target=target, count=1)

    if action == "cancel":
        target_action = _require_str(raw.get("target_action"), "target_action").lower()
        if target_action not in {"build", "train", "research", "morph_townhall"}:
            raise ActionValidationError(
                "cancel.target_action must be build, train, research, or morph_townhall"
            )
        target = _require_str(raw.get("target"), "target").lower()
        count = raw.get("count")
        if count is not None and (
            not isinstance(count, int) or isinstance(count, bool) or count < 1
        ):
            raise ActionValidationError("cancel count must be a positive integer when provided")
        return GameAction(
            action="cancel",
            target=target,
            target_action=target_action,
            count=count,
        )

    if action == "scan":
        if not known_target_names("scan", race=race):
            raise ActionValidationError("scan is not available for this race")
        target = _require_str(raw.get("target"), "target").lower()
        if not _ZONE_TARGET_RE.match(target):
            raise ActionValidationError("scan target must be a stable zone_id")
        return GameAction(action="scan", target=target, count=1)

    if action in {"call_mule", "supply_drop", "chrono_boost", "inject_larva", "spawn_creep_tumor"}:
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

    if action == "morph_townhall":
        target = _require_str(raw.get("target"), "target")
        if not _STRUCTURE_ID_RE.match(target):
            raise ActionValidationError(
                "morph_townhall.target must be a town hall id such as townhall_0"
            )
        to = _require_str(raw.get("to"), "to").lower()
        if to not in known_target_names("morph_townhall", race=race):
            raise ActionValidationError(
                f"morph_townhall.to must be one of {list(known_target_names('morph_townhall', race=race))}"
            )
        return GameAction(action="morph_townhall", target=target, to=to)

    if action == "combat":
        units_raw = raw.get("units")
        has_group = raw.get("group") is not None
        has_units = units_raw is not None
        normalized_units: Optional[Dict[str, int]] = None
        if has_units:
            if not isinstance(units_raw, Mapping) or not units_raw:
                raise ActionValidationError("combat.units must be a non-empty mapping")
            allowed_units = set(dispatchable_unit_names(race=race))
            normalized_units = {}
            for unit_name, count in units_raw.items():
                name = _require_str(unit_name, "units key").lower()
                if name not in allowed_units:
                    raise ActionValidationError(
                        f"combat.units key {name!r} is not a dispatchable unit"
                    )
                if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                    raise ActionValidationError(
                        f"combat.units[{name!r}] must be a positive integer"
                    )
                normalized_units[name] = int(count)
        source = raw.get("source")
        if source is not None:
            source = _require_str(source, "source")
            if not re.match(r"^group_(?:0|[1-9][0-9]*)$", source):
                raise ActionValidationError("combat.source must be group_0 or an outbound group id")
        destination = raw.get("destination")
        if destination is not None:
            destination = _require_str(destination, "destination")
            if not re.match(r"^group_(?:0|[1-9][0-9]*)$", destination):
                raise ActionValidationError(
                    "combat.destination must be group_0 or an outbound group id"
                )
            return GameAction(
                action="combat", group=destination if destination != "group_0" else None,
                destination=destination, units=normalized_units, source=source,
                public_action=public_action if public_action != "combat" else None,
            )
        if has_group and has_units:
            return GameAction(
                action="combat", group=str(raw["group"]), units=normalized_units,
                source=source or "group_0",
                public_action=public_action if public_action != "combat" else None,
            )
        style = _require_str(raw.get("style"), "style").lower()
        if style not in COMBAT_STYLES:
            raise ActionValidationError(f"combat.style must be one of {list(COMBAT_STYLES)}")
        target = _require_str(raw.get("target"), "target").lower()
        if not _ZONE_TARGET_RE.match(target):
            raise ActionValidationError("combat.target must be a stable zone_id")
        if has_group:
            return GameAction(
                action="combat", target=target, style=style, group=str(raw["group"]),
                public_action=public_action if public_action != "combat" else None,
            )
        return GameAction(
            action="combat",
            target=target,
            style=style,
            units=normalized_units,
            source=source,
            public_action=public_action if public_action != "combat" else None,
        )

    if action == "merge_groups":
        return GameAction(
            action="merge_groups",
            destination=_require_str(raw.get("destination"), "destination"),
            sources=tuple(str(item) for item in raw.get("sources") or ()),
            public_action=public_action if public_action != "merge_groups" else None,
        )

    if action == "retreat":
        if "method" not in raw or raw.get("method") in (None, ""):
            return GameAction(
                action="retreat", group=raw["group"],
                public_action=public_action if public_action != "retreat" else None,
            )
        method = raw.get("method")
        if method not in {"move", "recall"}:
            raise ActionValidationError("retreat.method must be move or recall")
        if method == "recall" and race != "protoss":
            raise ActionValidationError("retreat.method recall is only available for protoss")
        return GameAction(
            action="retreat", group=raw["group"], method=method,
            public_action=public_action if public_action != "retreat" else None,
        )

    raise ActionValidationError(f"unsupported action {action!r}")


def parse_decision(raw_actions: Sequence[Mapping[str, Any]] | None, *, race: str = "terran") -> DecisionBatch:
    """Parse one Agent decision of NormalizedToolCall objects. Requires a trailing advance."""
    _validate_race(race)
    internals = raw_actions
    normalizations = []
    if isinstance(raw_actions, Sequence) and not isinstance(raw_actions, (str, bytes)):
        internals = []
        for index, entry in enumerate(raw_actions):
            if isinstance(entry, Mapping):
                try:
                    call = parse_tool_call(entry, index=index)
                    internal = call.to_internal_entry()
                except ValueError as exc:
                    raise ActionValidationError(str(exc)) from exc
                internal, changes = normalize_target_aliases(internal, index=index, race=race)
                normalizations.extend(changes)
                internals.append(internal)
            else:
                internals.append(entry)
    try:
        validate_batch_shape(internals, race=race)
    except DecisionSchemaError as exc:
        raise _as_validation_error(exc) from exc

    assert raw_actions is not None
    raw_dicts: List[dict[str, Any]] = [dict(item) for item in raw_actions]
    # Validate normalized internal entries, but parse the original calls so
    # recordings and feedback retain the public tool names.
    game_actions = tuple(parse_game_action(entry, race=race) for entry in raw_dicts[:-1])
    advance = parse_advance_action(raw_dicts[-1], race=race)
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
                method=action.method,
                destination=action.destination,
                sources=action.sources,
                source=action.source,
                public_action=action.public_action,
            )
        )
    return DecisionBatch(
        actions=tuple(attached), advance=batch.advance, raw=batch.raw,
        normalizations=batch.normalizations,
        receipts_delivered=batch.receipts_delivered,
    )


def action_batch_to_dicts(actions: Iterable[GameAction] | DecisionBatch) -> List[dict[str, Any]]:
    if isinstance(actions, DecisionBatch):
        return actions.to_dicts()
    return [action.to_dict() for action in actions]
