"""Neutral tool contract shared by queries and actions."""
from __future__ import annotations


import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from sc2bench_env.interface.platform_rules import ACTION_RULES

ToolKind = Literal["knowledge", "read", "action"]
ToolStatus = Literal["ok", "staged", "no_op", "decision_ready", "rejected"]

LEGACY_ACTION_FORMAT_ERROR = (
    'legacy {"action": ...} format is not accepted; '
    'use {"name": "<tool>", "arguments": {...}}'
)


@dataclass(frozen=True)
class ToolCall:
    """Provider-independent tool call. Only name and parsed arguments."""

    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "arguments": dict(self.arguments)}

    def to_internal_entry(self) -> Dict[str, Any]:
        if "action" in self.arguments:
            raise ValueError("arguments must not contain action")
        return {"action": self.name, **self.arguments}


@dataclass(frozen=True)
class ToolResult:
    """One provider-neutral result for one ToolCall."""

    status: ToolStatus
    data: Mapping[str, Any] = field(default_factory=dict)
    decision_boundary: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "data": dict(self.data),
            "decision_boundary": self.decision_boundary,
        }


def rejected_result(code: str, message: str) -> ToolResult:
    return ToolResult(
        status="rejected",
        data={"error": {"code": code, "message": message}},
    )


def _schema_type_ok(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, Mapping)
    return True


def validate_tool_arguments(spec: ToolSpec, arguments: Mapping[str, Any]) -> Optional[str]:
    """Check required fields, types, enums and additionalProperties before execution."""
    if not isinstance(arguments, Mapping):
        return "arguments must be an object"
    parameters = spec.parameters or {}
    branches = parameters.get("oneOf")
    if isinstance(branches, list) and branches:
        errors = []
        for branch in branches:
            if not isinstance(branch, Mapping):
                continue
            error = validate_tool_arguments(
                ToolSpec(name=spec.name, kind=spec.kind, description=spec.description, parameters=branch),
                arguments,
            )
            if error is None:
                return None
            errors.append(error)
        if spec.name == "combat":
            return "arguments must match one combat form"
        return "; ".join(errors)
    properties = parameters.get("properties") or {}
    required = list(parameters.get("required") or [])
    if parameters.get("additionalProperties") is False:
        extra = sorted(str(key) for key in arguments if key not in properties)
        if extra:
            return f"unexpected fields: {extra}"
    missing = [name for name in required if name not in arguments]
    if missing:
        return f"missing fields: {missing}"
    for key, value in arguments.items():
        schema = properties.get(key)
        if not isinstance(schema, Mapping):
            continue
        expected = schema.get("type")
        if isinstance(expected, str) and not _schema_type_ok(value, expected):
            return f"{key} must be {expected}"
        enum = schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            return f"{key} must be one of {enum}"
        if expected in {"integer", "number"}:
            if isinstance(value, float) and not math.isfinite(value):
                return f"{key} must be a finite number"
            minimum = schema.get("minimum")
            if isinstance(minimum, (int, float)) and value < minimum:
                return f"{key} must be at least {minimum}"
            exclusive = schema.get("exclusiveMinimum")
            if isinstance(exclusive, (int, float)) and value <= exclusive:
                return f"{key} must be greater than {exclusive}"
        if expected == "array":
            minimum = schema.get("minItems")
            if isinstance(minimum, int) and isinstance(value, list) and len(value) < minimum:
                return f"{key} must contain at least {minimum} item"
            item = schema.get("items") if isinstance(schema.get("items"), Mapping) else {}
            item_type = item.get("type")
            if isinstance(item_type, str):
                for entry in value:
                    if not _schema_type_ok(entry, item_type):
                        return f"{key} items must be {item_type}"
            item_enum = item.get("enum")
            if isinstance(item_enum, list) and any(entry not in item_enum for entry in value):
                return f"{key} items must be one of {item_enum}"
            if schema.get("uniqueItems") is True and len({str(entry) for entry in value}) != len(value):
                return f"{key} must not contain duplicates"
        if expected == "object" and isinstance(value, Mapping):
            minimum = schema.get("minProperties")
            if isinstance(minimum, int) and len(value) < minimum:
                return f"{key} must contain at least {minimum} property"
    return None


def known_tool_names() -> frozenset[str]:
    names: set[str] = set()
    for race in ("terran", "protoss", "zerg"):
        names.update(spec.name for spec in tool_specs(race))
    return frozenset(names)


def parse_tool_call(raw: Any, *, index: int = 0) -> ToolCall:
    """Accept only {name, arguments}. Reject the old flat action object."""
    where = f"entry[{index}]"
    if not isinstance(raw, Mapping):
        raise ValueError(f"{where} must be an object")
    if "action" in raw and "name" not in raw:
        raise ValueError(LEGACY_ACTION_FORMAT_ERROR)
    extra = sorted(str(key) for key in raw.keys() if key not in {"name", "arguments"})
    if extra:
        raise ValueError(f"{where} only allows name and arguments; extra={extra}")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{where}.name must be a non-empty string")
    arguments = raw.get("arguments", {})
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, Mapping):
        raise ValueError(f"{where}.arguments must be an object")
    return ToolCall(name=name.strip(), arguments=dict(arguments))


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: ToolKind
    description: str
    parameters: Mapping[str, Any]


_KNOWLEDGE_RACES = ("terran", "protoss", "zerg")
_RACE_PROPERTY = {"type": "string", "enum": list(_KNOWLEDGE_RACES)}

READ_TOOLS = (
    "query_map_overview", "query_zone_state", "query_route",
    "query_production_state", "query_army_state",
)
KNOWLEDGE_TOOLS = (
    "query_unit_data", "query_building_data", "query_research_data",
    "query_prerequisite_path", "query_race_data",
)
ACTION_TOOLS = (
    "build", "train", "research", "cancel", "morph_townhall", "scout", "scan",
    "call_mule", "supply_drop", "dispatch_group", "attack_group", "defend_group",
    "reinforce_group", "merge_groups", "retreat_group", "advance",
)


def _kind(name: str) -> str:
    if name in READ_TOOLS:
        return "read"
    if name in KNOWLEDGE_TOOLS:
        return "knowledge"
    return "action"


def _spec(name: str, description: str, properties: Dict[str, Any], required: Sequence[str] = ()) -> ToolSpec:
    return ToolSpec(
        name=name,
        kind=_kind(name),
        description=description,
        parameters={
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
    )


def action_tool_names(race: str = "terran") -> Tuple[str, ...]:
    """Shared tools, plus race abilities that this catalog actually defines."""
    from sc2bench_env.catalog.races import get_catalog

    present = {spec.action for spec in get_catalog(race=race).targets}
    names = []
    for name in ACTION_TOOLS:
        if name in {"morph_townhall", "scan", "call_mule", "supply_drop"} and name not in present:
            continue
        names.append(name)
    combat_at = names.index("dispatch_group")
    for extra in ("chrono_boost", "inject_larva", "spawn_creep_tumor"):
        if extra in present:
            names.insert(combat_at, extra)
            combat_at += 1
    return tuple(names)


def _action_description(name: str, race: str) -> str:
    if race == "terran" and name == "build":
        return (
            "Request one additional building or add-on. After advance, the request remains active "
            "until construction starts. The platform chooses a legal placement or add-on host; "
            "Command Centers use expansion sites and Refineries require a free geyser at a ready "
            "owned town hall; Command Center upgrades use morph_townhall. Missing resources, "
            "prerequisites, a host, a free geyser or a legal "
            "placement leaves the request waiting and is reported in Changes."
        )
    if race == "protoss" and name == "build":
        return (
            "Request one additional structure or form. After advance, the request remains active "
            "until construction or conversion starts. The platform chooses a legal placement or "
            "source; Nexus uses an expansion site, Assimilator requires a free geyser at a ready "
            "owned town hall, and warpgate converts a completed Gateway after research. Missing "
            "resources, prerequisites, a source, a free geyser or a legal placement leaves the "
            "request waiting and is reported in Changes."
        )
    if race == "protoss" and name == "scout":
        return ACTION_RULES["scout"].replace("one SCV", "one Probe")
    if race == "zerg" and name == "build":
        return (
            "Request one additional structure or form. After advance, the request remains active "
            "until construction or morphing starts. The platform chooses a legal placement or "
            "source; Hatchery uses an expansion site, Extractor requires a free geyser at a ready "
            "owned town hall, and structure morphs consume their source. Missing resources, "
            "prerequisites, a Drone, a source, a free geyser or a legal placement leaves the "
            "request waiting and is reported in Changes. Overlords use train."
        )
    if name == "train":
        mechanism = {
            "terran": "The platform queues them at compatible producers.",
            "protoss": "The platform trains, warps in or merges them as required by the target.",
            "zerg": "The platform trains, hatches or morphs them as required by the target.",
        }[race]
        blockers = {
            "terran": "resources, supply, prerequisites or production capacity",
            "protoss": "resources, supply, prerequisites, source units or production capacity",
            "zerg": "resources, supply, prerequisites, source units or larva",
        }[race]
        return (
            "Request at least count additional target units. After advance, the request remains "
            "active until that minimum has been produced. " + mechanism + f" Missing {blockers} "
            "may leave part of the request waiting; Changes reports the observed blocker. Batch or "
            "morph mechanics may complete "
            "more than requested; later losses do not reopen completed production."
        )
    if name == "cancel":
        kinds = "build, train and research"
        if "morph_townhall" in action_tool_names(race):
            kinds = "build, train, research and morph_townhall"
        return (
            f"Remove matching unstarted {kinds} work from the persistent task queue. Optional "
            "count removes up to that quantity from the newest matching requests first; omit "
            "count to remove all matching unstarted work. Started or paid work remains."
        )
    if race == "zerg" and name == "scout":
        return ACTION_RULES["scout"].replace("one SCV", "one Drone")
    if race == "protoss" and name == "retreat_group":
        return (
            "Return an outbound group home. Survivors join group_0 only after arrival. Optional "
            "recall uses the main Nexus for nearby members; others walk. If recall is unavailable, "
            "the order fails and the current mission continues."
        )
    if name in {"dispatch_group", "attack_group", "defend_group"}:
        return ACTION_RULES[name]
    if name == "reinforce_group":
        return ACTION_RULES[name]
    if name == "merge_groups":
        return ACTION_RULES[name]
    if name == "retreat_group":
        return ACTION_RULES[name]
    if race == "terran" and name == "morph_townhall":
        return (
            "Morph the selected Command Center, identified by a townhall ID from Structures, into "
            "an Orbital Command or Planetary Fortress. The request completes when a native morph order or the resulting form is observed. "
            "The new form may still be unfinished."
        )
    if race == "zerg" and name == "morph_townhall":
        return (
            "Morph the selected Hatchery or Lair, identified by a townhall ID from Structures, "
            "into a Lair or Hive respectively. The request completes when a native morph order "
            "or the resulting form is observed. The new form may still be unfinished."
        )
    return ACTION_RULES[name]


def _action_specs(race: str = "terran") -> Tuple[ToolSpec, ...]:
    from sc2bench_env.interface.decision_rules import action_tool_argument_schema

    schemas = []
    available_actions = action_tool_names(race)
    for name in available_actions:
        if name == "dispatch_group":
            source = {"type": "string", "description": "group_0 or an active outbound group ID"}
            units = {
                "type": "object", "minProperties": 1,
                "description": "dispatchable unit name to positive count",
                "additionalProperties": {"type": "integer", "minimum": 1},
            }
            schemas.append(ToolSpec(
                name=name, kind="action", description=_action_description(name, race),
                parameters={
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "source": source,
                        "units": units,
                        "order": {"type": "string", "enum": ["attack", "defend"]},
                        "target": {"type": "string"},
                    },
                    "required": ["source", "units", "order", "target"],
                },
            ))
            continue
        if name in {"attack_group", "defend_group"}:
            schemas.append(_spec(name, _action_description(name, race), {
                "group": {
                    "type": "string",
                    "description": "active outbound group ID, such as group_1",
                },
                "target": {"type": "string"},
            }, ("group", "target")))
            continue
        if name == "reinforce_group":
            schemas.append(_spec(name, _action_description(name, race), {
                "source": {"type": "string", "description": "group_0 or an active outbound group ID"},
                "destination": {"type": "string", "description": "group_0 or an active outbound group ID"},
                "units": {
                    "type": "object", "minProperties": 1,
                    "description": "dispatchable unit name to positive count",
                    "additionalProperties": {"type": "integer", "minimum": 1},
                },
            }, ("source", "destination", "units")))
            continue
        if name == "retreat_group":
            properties = {
                "group": {"type": "string", "description": "existing outbound group ID"},
                "method": {
                    "type": "string", "enum": ["move", "recall"],
                    "description": "move, or recall on Protoss",
                },
            }
            if race != "protoss":
                properties.pop("method")
            schemas.append(_spec(
                name, _action_description(name, race), properties, ("group",),
            ))
            continue
        properties, required = action_tool_argument_schema(name)
        if name == "cancel":
            target_action = dict(properties["target_action"])
            target_action["enum"] = [
                action for action in target_action["enum"]
                if action in available_actions
            ]
            schemas.append(ToolSpec(
                name=name,
                kind=_kind(name),
                description=_action_description(name, race),
                parameters={
                    "type": "object",
                    "properties": {
                        "target_action": target_action,
                        "target": properties["target"],
                        "count": properties["count"],
                    },
                    "required": ["target_action", "target"],
                    "additionalProperties": False,
                },
            ))
            continue
        schemas.append(_spec(name, _action_description(name, race), properties, required))
    return tuple(schemas)


def tool_specs(race: str = "terran") -> Tuple[ToolSpec, ...]:
    """Compact OpenAI-style function tools; no catalog enums in descriptions."""
    return (
        _spec(
            "query_map_overview",
            "Read known zone topology and corridor distances. Does not scout or reveal enemies.",
            {},
        ),
        _spec(
            "query_zone_state",
            "Read current or last-seen state and known resources for named zones, or all zones when zone_ids is omitted. Does not scout.",
            {"zone_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}}},
        ),
        _spec(
            "query_route",
            "Read the shortest known walkable zone path. Does not reveal enemies.",
            {"from_zone": {"type": "string"}, "to_zone": {"type": "string"}},
            ("from_zone", "to_zone"),
        ),
        _spec(
            "query_production_state",
            "Read persistent production work, execution blockers and infrastructure, optionally filtered.",
            {
                "actions": {"type": "array", "minItems": 1, "items": {"type": "string", "enum": ["build", "train", "research", "morph_townhall"]}},
                "targets": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            },
        ),
        _spec(
            "query_army_state",
            "Read detailed membership, activity and transport state for selected or all groups.",
            {"groups": {"type": "array", "minItems": 1, "items": {"type": "string"}}},
        ),
        _spec(
            "query_unit_data",
            "Get costs, supply, production, prerequisites, roles, movement, durability and weapons for units.",
            {"race": _RACE_PROPERTY, "names": {"type": "array", "minItems": 1, "items": {"type": "string"}}},
            ("race", "names"),
        ),
        _spec(
            "query_building_data",
            "Get costs, time, builder, prerequisites, supply, roles and weapons for buildings.",
            {"race": _RACE_PROPERTY, "names": {"type": "array", "minItems": 1, "items": {"type": "string"}}},
            ("race", "names"),
        ),
        _spec(
            "query_research_data",
            "Get cost, duration, facility, prerequisites and effects for research.",
            {"race": _RACE_PROPERTY, "names": {"type": "array", "minItems": 1, "items": {"type": "string"}}},
            ("race", "names"),
        ),
        _spec(
            "query_prerequisite_path",
            "Get one target's static technology dependency path, not a match build order.",
            {"race": _RACE_PROPERTY, "target": {"type": "string"}},
            ("race", "target"),
        ),
        _spec(
            "query_race_data",
            "List a race's units, buildings, research, structure morphs and platform abilities.",
            {"race": _RACE_PROPERTY},
            ("race",),
        ),
        *_action_specs(race),
    )

