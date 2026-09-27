"""Neutral tool contract shared by queries and actions."""
from __future__ import annotations


from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from sc2bench_env.interface.platform_rules import ACTION_RULES

ToolKind = Literal["knowledge", "read", "action"]
ToolStatus = Literal["ok", "staged", "decision_ready", "rejected"]

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
        if expected == "array":
            item = schema.get("items") if isinstance(schema.get("items"), Mapping) else {}
            item_type = item.get("type")
            if isinstance(item_type, str):
                for entry in value:
                    if not _schema_type_ok(entry, item_type):
                        return f"{key} items must be {item_type}"
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

READ_TOOLS = ("query_map_overview", "query_zone_state", "query_route")
KNOWLEDGE_TOOLS = (
    "query_unit_data", "query_building_data", "query_research_data",
    "query_prerequisite_path", "query_race_data",
)
ACTION_TOOLS = (
    "build", "train", "research", "cancel", "upgrade", "scout", "scan",
    "call_mule", "combat", "retreat", "advance",
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
        if name in {"upgrade", "scan", "call_mule"} and name not in present:
            continue
        names.append(name)
    combat_at = names.index("combat")
    for extra in ("chrono_boost", "inject_larva", "spawn_creep_tumor"):
        if extra in present:
            names.insert(combat_at, extra)
            combat_at += 1
    return tuple(names)


def _action_description(name: str, race: str) -> str:
    if race == "protoss" and name == "build":
        return (
            "Build one additional structure; placement is automatic, and Nexuses expand. "
            "Assimilators require a free geyser at a ready owned Nexus. Pylons are not built "
            "automatically. Completes when construction starts and an unfinished structure appears."
        )
    if race == "protoss" and name == "scout":
        return ACTION_RULES["scout"].replace("one SCV", "one Probe")
    if race == "zerg" and name == "build":
        return (
            "Build one additional structure; placement is automatic, and Hatcheries expand. "
            "Extractors require a free geyser at a ready owned town hall. Overlords are trained, "
            "not built automatically. Lurker Den morphs a Hydralisk Den, and Greater Spire morphs "
            "a Spire. Completes when construction or the morph starts."
        )
    if race == "zerg" and name == "scout":
        return ACTION_RULES["scout"].replace("one SCV", "one Drone")
    if race == "zerg" and name == "upgrade":
        return (
            "Morph the selected Hatchery into a Lair, or the selected Lair into a Hive. "
            "The request completes when issued, not when the morph finishes."
        )
    return ACTION_RULES[name]


def _action_specs(race: str = "terran") -> Tuple[ToolSpec, ...]:
    from sc2bench_env.interface.decision_rules import action_tool_argument_schema

    schemas = []
    for name in action_tool_names(race):
        properties, required = action_tool_argument_schema(name)
        schemas.append(_spec(name, _action_description(name, race), properties, required))
    return tuple(schemas)


def tool_specs(race: str = "terran") -> Tuple[ToolSpec, ...]:
    """Compact OpenAI-style function tools; no catalog enums in descriptions."""
    return (
        _spec(
            "query_map_overview",
            "Use when selecting zones, targets or routes without verified map topology.",
            {},
        ),
        _spec(
            "query_zone_state",
            "Use when a decision depends on the current or last-seen state of specific zones.",
            {"zone_ids": {"type": "array", "items": {"type": "string"}}},
            ("zone_ids",),
        ),
        _spec(
            "query_route",
            "Use when movement or target selection depends on the route or distance between two zones. Does not reveal hidden enemies.",
            {"from_zone": {"type": "string"}, "to_zone": {"type": "string"}},
            ("from_zone", "to_zone"),
        ),
        _spec(
            "query_unit_data",
            "Get cost, supply, production, prerequisites, roles, movement and available durability and weapon data for the named units.",
            {"race": _RACE_PROPERTY, "names": {"type": "array", "items": {"type": "string"}}},
            ("race", "names"),
        ),
        _spec(
            "query_building_data",
            "Get cost, build time, builder, prerequisites, supply provided, roles and available weapon data for the named buildings.",
            {"race": _RACE_PROPERTY, "names": {"type": "array", "items": {"type": "string"}}},
            ("race", "names"),
        ),
        _spec(
            "query_research_data",
            "Get cost, duration, facility, prerequisites and recorded effects for the named research items.",
            {"race": _RACE_PROPERTY, "names": {"type": "array", "items": {"type": "string"}}},
            ("race", "names"),
        ),
        _spec(
            "query_prerequisite_path",
            "Get the static technology dependency path for one target. This is not a match build order.",
            {"race": _RACE_PROPERTY, "target": {"type": "string"}},
            ("race", "target"),
        ),
        _spec(
            "query_race_data",
            "List the canonical controllable and observable units, buildings, research items, structure morphs and platform abilities for one race.",
            {"race": _RACE_PROPERTY},
            ("race",),
        ),
        *_action_specs(race),
    )

