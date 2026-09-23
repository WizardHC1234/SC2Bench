"""Single-source decision field rules for Schema + parser.

Owns required / optional / forbidden fields for each action verb.
`decision_json_schema()` and `parse_decision()` both consume these definitions
so they cannot drift apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from sc2bench_env.interface.action_catalog import (
    ACTION_VERBS,
    COMBAT_STYLES,
    decision_examples,
    known_target_names,
)

from sc2bench_env.interface.races import require_supported_own_race
from sc2bench_env.interface.observations import WORKER_UNIT_NAMES
from sc2bench_env.interface.tools import (
    LEGACY_ACTION_FORMAT_ERROR,
    parse_normalized_tool_call,
)

ZONE_PATTERN = r"^zone_[A-Za-z0-9_]+$"
# Townhall object ids currently exposed as cc_<n>.
STRUCTURE_ID_PATTERN = r"^cc_[A-Za-z0-9_]+$"
GROUP_PATTERN = r"^group_[1-9][0-9]*$"

# Model JSON must never include these keys on any entry.
GLOBAL_FORBIDDEN_FIELDS: Tuple[str, ...] = ("action_id",)


@dataclass(frozen=True)
class VerbFieldRule:
    """Allowlisted fields for one action verb (besides discriminant `action`)."""

    verb: str
    required: Tuple[str, ...] = ()
    optional: Tuple[str, ...] = ()

    @property
    def allowed(self) -> frozenset[str]:
        return frozenset(("action",) + self.required + self.optional)


VERB_FIELD_RULES: Dict[str, VerbFieldRule] = {
    "build": VerbFieldRule("build", required=("target",)),
    "train": VerbFieldRule("train", required=("target", "count")),
    "research": VerbFieldRule("research", required=("target",)),
    "cancel": VerbFieldRule("cancel", required=("target_action", "target")),
    "scan": VerbFieldRule("scan", required=("target",)),
    "call_mule": VerbFieldRule("call_mule"),
    "chrono_boost": VerbFieldRule("chrono_boost"),
    "inject_larva": VerbFieldRule("inject_larva"),
    "spawn_creep_tumor": VerbFieldRule("spawn_creep_tumor"),
    "scout": VerbFieldRule("scout", required=("route",)),
    "upgrade": VerbFieldRule("upgrade", required=("target", "to")),
    "combat": VerbFieldRule("combat", required=("style", "target"), optional=("units", "group")),
    "retreat": VerbFieldRule("retreat", required=("group",)),
    "advance": VerbFieldRule("advance", required=("seconds",)),
}


class DecisionSchemaError(ValueError):
    """Raised when a decision fails shared Schema/field rules."""


def _unknown_keys(raw: Mapping[str, Any], allowed: Iterable[str]) -> List[str]:
    allowed_set = set(allowed)
    return sorted(str(key) for key in raw.keys() if key not in allowed_set)


def reject_forbidden_global_fields(raw: Mapping[str, Any], *, where: str) -> None:
    for key in GLOBAL_FORBIDDEN_FIELDS:
        if key in raw:
            raise DecisionSchemaError(
                f"{where}: {key} is harness-only; use Environment.step(retry_ids=...)"
            )


def reject_unknown_fields(raw: Mapping[str, Any], rule: VerbFieldRule) -> None:
    unknown = _unknown_keys(raw, rule.allowed)
    if unknown:
        raise DecisionSchemaError(
            f"{rule.verb} rejects unknown fields {unknown}; allowed={sorted(rule.allowed)}"
        )


def require_fields(raw: Mapping[str, Any], rule: VerbFieldRule) -> None:
    missing = [name for name in rule.required if name not in raw]
    if missing:
        raise DecisionSchemaError(f"{rule.verb} missing required fields {missing}")


def validate_entry_fields(raw: Mapping[str, Any], *, index: int, race: str = "terran") -> str:
    """Validate one NormalizedToolCall; return its action verb."""
    try:
        require_supported_own_race(race)
    except ValueError as exc:
        raise DecisionSchemaError(str(exc)) from exc
    try:
        call = parse_normalized_tool_call(raw, index=index)
    except ValueError as exc:
        raise DecisionSchemaError(str(exc)) from exc
    try:
        internal = call.to_internal_entry()
    except ValueError as exc:
        raise DecisionSchemaError(f"entry[{index}]: {exc}") from exc
    reject_forbidden_global_fields(internal, where=f"entry[{index}]")
    verb = call.name.strip().lower()
    if verb == "wait":
        raise DecisionSchemaError(
            "wait is no longer supported; end with "
            '{"name":"advance","arguments":{"seconds":<positive_number>}}'
        )
    rule = VERB_FIELD_RULES.get(verb)
    if rule is None:
        raise DecisionSchemaError(
            f"unsupported action {call.name!r}; allowed={list(ACTION_VERBS)}"
        )
    reject_unknown_fields(internal, rule)
    require_fields(internal, rule)
    _validate_entry_values(internal, verb=verb, index=index, race=race)
    return verb


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_entry_values(raw: Mapping[str, Any], *, verb: str, index: int, race: str) -> None:
    where = f"entry[{index}]"
    if verb == "build":
        target = raw.get("target")
        if not isinstance(target, str) or not target.strip():
            raise DecisionSchemaError(f"{where}.target must be a non-empty string")
        normalized = target.strip().lower()
        if normalized in {"orbital_command", "planetary_fortress"}:
            raise DecisionSchemaError(
                f"use upgrade with a structures[].id to morph {normalized}; "
                'example: {"name":"upgrade","arguments":{"target":"cc_0","to":"orbital_command"}}'
            )
        if normalized in {"lair", "hive"}:
            raise DecisionSchemaError(
                f"use upgrade with a structures[].id to morph {normalized}; "
                '{"name":"upgrade","arguments":{"target":"cc_0","to":"lair"}}'
            )
        if normalized not in known_target_names("build", race=race):
            raise DecisionSchemaError(
                f"unsupported build target {normalized!r}; "
                f"allowed={list(known_target_names('build', race=race))}"
            )
    elif verb == "train":
        target = raw.get("target")
        count = raw.get("count")
        if not isinstance(target, str) or not target.strip():
            raise DecisionSchemaError(f"{where}.target must be a non-empty string")
        if not _is_positive_int(count):
            raise DecisionSchemaError(f"{where}.count must be a positive integer")
        normalized = target.strip().lower()
        if normalized not in known_target_names("train", race=race):
            raise DecisionSchemaError(
                f"unsupported train target {normalized!r}; "
                f"allowed={list(known_target_names('train', race=race))}"
            )
    elif verb == "research":
        target = raw.get("target")
        if not isinstance(target, str) or not target.strip():
            raise DecisionSchemaError(f"{where}.target must be a non-empty string")
        normalized = target.strip().lower()
        if normalized not in known_target_names("research", race=race):
            raise DecisionSchemaError(
                f"unsupported research target {normalized!r}; "
                f"allowed={list(known_target_names('research', race=race))}"
            )
    elif verb == "cancel":
        target_action = raw.get("target_action")
        target = raw.get("target")
        if not isinstance(target_action, str) or target_action.strip().lower() not in {
            "build",
            "train",
            "research",
        }:
            raise DecisionSchemaError(
                f"{where}.target_action must be build, train, or research"
            )
        if not isinstance(target, str) or not target.strip():
            raise DecisionSchemaError(f"{where}.target must be a non-empty string")
    elif verb == "scan":
        target = raw.get("target")
        if not isinstance(target, str) or not re.match(ZONE_PATTERN, target.strip().lower()):
            raise DecisionSchemaError(f"{where}.target must be a zone_id")
    elif verb == "scout":
        route = raw.get("route")
        if route == "all":
            return
        if not isinstance(route, Sequence) or isinstance(route, (str, bytes)) or not route:
            raise DecisionSchemaError(f"{where}.route must be a non-empty array of zone_ids")
        for item in route:
            if not isinstance(item, str) or not re.match(ZONE_PATTERN, item.strip().lower()):
                raise DecisionSchemaError(f"{where}.route items must be zone_ids")
    elif verb == "upgrade":
        target = raw.get("target")
        to = raw.get("to")
        if not isinstance(target, str) or not re.match(STRUCTURE_ID_PATTERN, target.strip()):
            raise DecisionSchemaError(
                f"{where}.target must be a structures[].id such as cc_0"
            )
        if not isinstance(to, str) or to.strip().lower() not in known_target_names("upgrade", race=race):
            raise DecisionSchemaError(
                f"{where}.to must be one of {list(known_target_names('upgrade', race=race))}"
            )
    elif verb == "retreat":
        if not isinstance(raw.get("group"), str) or not re.fullmatch(GROUP_PATTERN, raw["group"]):
            raise DecisionSchemaError(f"{where}.group must reference an outbound group such as group_1, not group_0; use the full quoted ID, not a numeric index; received {raw.get('group')!r}")
    elif verb == "combat":
        style = raw.get("style")
        target = raw.get("target")
        units = raw.get("units")
        if not isinstance(style, str) or style.strip().lower() not in COMBAT_STYLES:
            raise DecisionSchemaError(
                f"{where}.style must be one of {list(COMBAT_STYLES)}"
            )
        if not isinstance(target, str) or not re.match(ZONE_PATTERN, target.strip().lower()):
            raise DecisionSchemaError(f"{where}.target must be a zone_id string copied from Observation, not a numeric index; received {target!r}")
        if "group" in raw:
            if "units" in raw:
                raise DecisionSchemaError(f"{where}: group orders cannot include units or change membership")
            if not isinstance(raw["group"], str) or not re.fullmatch(GROUP_PATTERN, raw["group"]):
                raise DecisionSchemaError(f"{where}.group must reference an outbound group such as group_1, not group_0; use the full quoted ID, not a numeric index; received {raw['group']!r}")
            return
        if not isinstance(units, Mapping) or not units:
            raise DecisionSchemaError(f"{where}.units must be a non-empty object")
        train_names = set(known_target_names("train", race=race)) - WORKER_UNIT_NAMES
        for unit_name, count in units.items():
            if not isinstance(unit_name, str) or unit_name.strip().lower() not in train_names:
                raise DecisionSchemaError(
                    f"{where}.units keys must be army train targets; "
                    f"got {unit_name!r}"
                )
            if not _is_positive_int(count):
                raise DecisionSchemaError(
                    f"{where}.units[{unit_name!r}] must be a positive integer"
                )
    elif verb == "advance":
        seconds = raw.get("seconds")
        if not _is_number(seconds) or seconds <= 0:
            raise DecisionSchemaError(f"{where}.seconds must be a positive number")


def validate_batch_shape(raw_actions: Sequence[Mapping[str, Any]] | None, *, race: str = "terran") -> None:
    """Batch-level rules that JSON Schema items alone cannot express."""
    if raw_actions is None:
        raise DecisionSchemaError("decision must be a JSON array ending with advance")
    if not isinstance(raw_actions, Sequence) or isinstance(raw_actions, (str, bytes)):
        raise DecisionSchemaError("decision must be a JSON array")
    if len(raw_actions) < 1:
        raise DecisionSchemaError("decision must include a trailing advance")

    verbs: List[str] = []
    for index, entry in enumerate(raw_actions):
        verbs.append(validate_entry_fields(entry, index=index, race=race))

    if verbs[-1] != "advance":
        raise DecisionSchemaError("decision must end with exactly one advance action")
    if any(verb == "advance" for verb in verbs[:-1]):
        raise DecisionSchemaError("advance is only allowed as the final decision entry")


def _string_enum(values: Sequence[str]) -> Dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def _zone_string() -> Dict[str, Any]:
    return {"type": "string", "pattern": ZONE_PATTERN}


def _structure_id_string() -> Dict[str, Any]:
    return {"type": "string", "pattern": STRUCTURE_ID_PATTERN}


def action_tool_argument_schema(verb: str) -> Tuple[Dict[str, Any], Tuple[str, ...]]:
    """Compact argument schema for one Action Tool. No catalog enums."""
    rule = VERB_FIELD_RULES[verb]
    properties: Dict[str, Any] = {
        "build": {"target": {"type": "string"}},
        "train": {
            "target": {"type": "string"},
            "count": {"type": "integer", "minimum": 1},
        },
        "research": {"target": {"type": "string"}},
        "cancel": {
            "target_action": {"type": "string", "enum": ["build", "train", "research"]},
            "target": {"type": "string"},
        },
        "scan": {"target": {"type": "string"}},
        "call_mule": {},
        "chrono_boost": {},
        "inject_larva": {},
        "spawn_creep_tumor": {},
        "scout": {
            "route": {
                "description": 'ordered zone_id array, or the string "all"',
                "oneOf": [
                    {"type": "string", "const": "all"},
                    {"type": "array", "minItems": 1, "items": {"type": "string"}},
                ],
            }
        },
        "upgrade": {
            "target": {"type": "string", "description": "structure ID from Observation"},
            "to": {"type": "string", "description": "morph name"},
        },
        "combat": {
            "style": {"type": "string", "enum": list(COMBAT_STYLES)},
            "target": {"type": "string"},
            "units": {
                "type": "object",
                "description": "free unit name to count, dispatched from group_0",
                "additionalProperties": {"type": "integer", "minimum": 1},
            },
            "group": {"type": "string", "description": "existing outbound group ID"},
        },
        "retreat": {"group": {"type": "string", "description": "existing outbound group ID"}},
        "advance": {"seconds": {"type": "number", "exclusiveMinimum": 0}},
    }[verb]
    return properties, rule.required


def _tool_call_object_schema(
    verb: str,
    *,
    properties: Dict[str, Any],
    required: Sequence[str],
) -> Dict[str, Any]:
    return {
        "type": "object",
        "required": ["name", "arguments"],
        "properties": {
            "name": {"type": "string", "const": verb},
            "arguments": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }


def decision_json_schema(*, race: str = "terran") -> Dict[str, Any]:
    """Platform-owned JSON Schema for one NormalizedToolCall array (draft-07)."""
    require_supported_own_race(race)
    build_targets = list(known_target_names("build", race=race))
    train_targets = list(known_target_names("train", race=race))
    research_targets = list(known_target_names("research", race=race))
    morph_targets = list(known_target_names("upgrade", race=race))

    item_schemas = [
        _tool_call_object_schema(
            "build",
            properties={"target": _string_enum(build_targets)},
            required=("target",),
        ),
        _tool_call_object_schema(
            "train",
            properties={
                "target": _string_enum(train_targets),
                "count": {"type": "integer", "minimum": 1},
            },
            required=("target", "count"),
        ),
        _tool_call_object_schema(
            "research",
            properties={"target": _string_enum(research_targets)},
            required=("target",),
        ),
        _tool_call_object_schema(
            "cancel",
            properties={
                "target_action": {"type": "string", "enum": ["build", "train", "research"]},
                "target": {"type": "string", "minLength": 1},
            },
            required=("target_action", "target"),
        ),
        _tool_call_object_schema(
            "scan",
            properties={"target": _zone_string()},
            required=("target",),
        ),
        _tool_call_object_schema("call_mule", properties={}, required=()),
        _tool_call_object_schema("chrono_boost", properties={}, required=()),
        _tool_call_object_schema("inject_larva", properties={}, required=()),
        _tool_call_object_schema("spawn_creep_tumor", properties={}, required=()),
        _tool_call_object_schema(
            "scout",
            properties={
                "route": {"oneOf": [
                    {"type": "string", "const": "all"},
                    {"type": "array", "minItems": 1, "items": _zone_string()},
                ]}
            },
            required=("route",),
        ),
        _tool_call_object_schema(
            "upgrade",
            properties={
                "target": _structure_id_string(),
                "to": _string_enum(morph_targets),
            },
            required=("target", "to"),
        ),
        _tool_call_object_schema(
            "retreat",
            properties={"group": {"type": "string", "pattern": GROUP_PATTERN}},
            required=("group",),
        ),
        _tool_call_object_schema(
            "combat",
            properties={"style": _string_enum(list(COMBAT_STYLES)), "target": _zone_string(),
                        "group": {"type": "string", "pattern": GROUP_PATTERN}},
            required=("style", "target", "group"),
        ),
        _tool_call_object_schema(
            "combat",
            properties={
                "style": _string_enum(list(COMBAT_STYLES)),
                "target": _zone_string(),
                "units": {
                    "type": "object",
                    "minProperties": 1,
                    "additionalProperties": {"type": "integer", "minimum": 1},
                },
            },
            required=("style", "target", "units"),
        ),
        _tool_call_object_schema(
            "advance",
            properties={"seconds": {"type": "number", "exclusiveMinimum": 0}},
            required=("seconds",),
        ),
    ]
    item_schemas = [
        schema for schema in item_schemas
        if schema["properties"]["name"]["const"] not in {
            "scan", "call_mule", "chrono_boost", "inject_larva", "spawn_creep_tumor", "upgrade",
        }
        or known_target_names(schema["properties"]["name"]["const"], race=race)
    ]

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "SC2BenchDecision",
        "type": "array",
        "minItems": 1,
        "items": {"oneOf": item_schemas},
        "examples": list(decision_examples(race=race)),
        "x-sc2bench-batch-rules": {
            "trailing_advance_required": True,
            "advance_only_at_end": True,
            "action_id_forbidden_in_model_json": True,
            "retry_ids_via": "Environment.step(..., retry_ids=[...])",
        },
        "x-sc2bench-targets": {
            "build": build_targets,
            "train": train_targets,
            "research": research_targets,
            "upgrade.to": morph_targets,
        },
    }


def schema_accepts_entry(raw: Mapping[str, Any], *, race: str = "terran") -> Optional[str]:
    """Return None if entry matches field rules; else an error message.

    Used by consistency tests. Semantic catalog checks still live in the parser.
    """
    try:
        validate_entry_fields(raw, index=0, race=race)
        return None
    except DecisionSchemaError as exc:
        return str(exc)
