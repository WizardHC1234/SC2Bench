"""Read, Knowledge and Action tool schemas. Only Read/Knowledge execute here."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sc2bench_env.interface.action_catalog import get_target
from sc2bench_env.interface.catalogs import get_catalog
from sc2bench_env.interface.platform_rules import ACTION_RULES
from sc2bench_env.interface.races import require_supported_own_race

LEGACY_ACTION_FORMAT_ERROR = (
    'legacy {"action": ...} format is not accepted; '
    'use {"name": "<tool>", "arguments": {...}}'
)


@dataclass(frozen=True)
class NormalizedToolCall:
    """Provider-independent tool call. Only name and parsed arguments."""

    name: str
    arguments: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "arguments": dict(self.arguments)}

    def to_internal_entry(self) -> Dict[str, Any]:
        if "action" in self.arguments:
            raise ValueError("arguments must not contain action")
        return {"action": self.name, **self.arguments}


def parse_normalized_tool_call(
    raw: Any, *, index: int = 0,
) -> NormalizedToolCall:
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
    return NormalizedToolCall(name=name.strip(), arguments=dict(arguments))


def queued_tool_result(call: NormalizedToolCall) -> Dict[str, Any]:
    return {"status": "queued", "call": call.to_dict()}


def _display_value(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _display_list(value: Any) -> str:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return "none"
    items = [str(item) for item in value]
    return ", ".join(items) if items else "none"


def _display_supply_provided(value: Any) -> str:
    if value in (0, 0.0, "0", "0.0"):
        return "none"
    return _display_value(value)


def _display_counts(value: Any) -> str:
    if not isinstance(value, Mapping) or not value:
        return "none"
    return ", ".join(f"{name} x{count}" for name, count in value.items())


def _render_catalog_results(result: Mapping[str, Any], *, kind: str) -> str:
    headings = {
        "unit": "Unit data",
        "building": "Building data",
        "research": "Research data",
    }
    lines = [headings[kind]]
    rows = result.get("results")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        return "\n".join((*lines, "No results."))
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = _display_value(row.get("name"))
        if row.get("error"):
            extra = ""
            if row.get("candidates"):
                extra = f"; close candidates: {_display_list(row.get('candidates'))}"
            lines.append(f"- {name}: error: {_display_value(row.get('error'))}{extra}")
            continue
        lines.append(f"- {name}")
        lines.append(
            "  Cost: "
            f"{_display_value(row.get('minerals'))} minerals, "
            f"{_display_value(row.get('vespene'))} gas"
        )
        time_label = "Training time" if kind == "unit" else (
            "Build time" if kind == "building" else "Research time"
        )
        lines.append(f"  {time_label}: {_display_value(row.get('time_seconds'))} seconds")
        if kind == "unit":
            lines.append(f"  Supply required by this action: {_display_value(row.get('supply'))}")
            lines.append(f"  Total supply while alive: {_display_value(row.get('client_food_required'))}")
            lines.append(f"  Supply provided when complete: {_display_supply_provided(row.get('food_provided'))}")
            lines.append(f"  Produced at: {_display_value(row.get('produced_at'))}")
        elif kind == "building":
            lines.append(f"  Builder: {_display_value(row.get('builder'))}")
            lines.append(f"  Kind: {_display_value(row.get('kind'))}")
            lines.append(f"  Supply provided when complete: {_display_supply_provided(row.get('food_provided'))}")
            if row.get("morph_from"):
                lines.append(f"  Morphs from: {_display_value(row.get('morph_from'))}")
        else:
            lines.append(f"  Facility: {_display_value(row.get('facility'))}")
        lines.append(f"  Prerequisites: {_display_list(row.get('prerequisites'))}")
        lines.append(f"  Availability: {_display_value(row.get('availability'))}")
        if kind == "research":
            lines.append(f"  Effect: {_display_value(row.get('description'))}")
            continue
        lines.append(f"  Roles: {_display_list(row.get('roles'))}")
        gaps = [
            label for label, key in (
                ("health", "health"), ("shields", "shields"), ("energy_max", "energy_max"),
            ) if row.get(key) == "unknown"
        ]
        if gaps:
            lines.append(f"  Data gaps: {', '.join(gaps)}")
        else:
            lines.append(f"  Health: {_display_value(row.get('health'))}")
            lines.append(f"  Shields: {_display_value(row.get('shields'))}")
            lines.append(f"  Energy max: {_display_value(row.get('energy_max'))}")
        lines.append(f"  Armor: {_display_value(row.get('armor'))}")
        if kind == "unit":
            lines.append(f"  Movement speed: {_display_value(row.get('movement_speed'))}")
        lines.append(f"  Sight: {_display_value(row.get('sight_range'))}")
        lines.append(f"  Attributes: {_display_list(row.get('attributes'))}")
        attack = row.get("can_attack")
        lines.append(
            "  Can attack: varies by form"
            if attack == "varies_by_form" else f"  Can attack: {_display_value(attack)}"
        )
        if row.get("form_note"):
            lines.append(f"  {_display_value(row.get('form_note'))}")
        forms = row.get("forms")
        if isinstance(forms, Sequence) and not isinstance(forms, (str, bytes)) and forms:
            lines.append("  Forms:")
            for form in forms:
                if not isinstance(form, Mapping):
                    continue
                lines.append(
                    f"  - {_display_value(form.get('proto_name'))} "
                    f"id {_display_value(form.get('unit_id'))}, "
                    f"movement {_display_value(form.get('movement_layer'))}, "
                    f"can_attack {_display_value(form.get('can_attack'))}"
                )
                for weapon in form.get("weapons") or []:
                    lines.append(f"    Weapon: {_display_value(weapon)}")
        platform = row.get("platform_behavior")
        if (platform is not None and platform not in ("unknown", "not_applicable")
                and platform != row.get("description") and platform != row.get("form_note")):
            lines.append(f"  Platform behavior: {_display_value(platform)}")
        lines.append(f"  Description: {_display_value(row.get('description'))}")
    return "\n".join(lines)


def _render_map_overview(result: Mapping[str, Any]) -> str:
    topology = result.get("map_topology")
    if not isinstance(topology, Mapping):
        return "Map overview\nNo map topology is available."
    lines = ["Map overview"]
    lines.append(
        f"Distance basis: {_display_value(topology.get('distance_basis'))}; "
        f"neighbor basis: {_display_value(topology.get('neighbor_basis'))}"
    )
    lines.append(
        "Path coverage: "
        f"{_display_value(topology.get('verified_path_pair_count'))}/"
        f"{_display_value(topology.get('total_path_pair_count'))} verified pairs"
    )
    zones = topology.get("zones")
    if not isinstance(zones, Sequence) or isinstance(zones, (str, bytes)) or not zones:
        lines.append("Zones: none")
        return "\n".join(lines)
    lines.append("Zones:")
    for zone in zones:
        if not isinstance(zone, Mapping):
            continue
        neighbors = []
        for neighbor in zone.get("corridor_neighbors") or []:
            if isinstance(neighbor, Mapping):
                neighbors.append(
                    f"{_display_value(neighbor.get('zone_id'))} "
                    f"({_display_value(neighbor.get('path_distance'))})"
                )
        lines.append(
            f"- {_display_value(zone.get('zone_id'))}: "
            f"own-main distance={_display_value(zone.get('path_distance_from_own_main'))}; "
            f"enemy-main distance={_display_value(zone.get('path_distance_to_enemy_main'))}; "
            f"ramp={_display_value(zone.get('has_ramp'))}; "
            f"neighbors={', '.join(neighbors) if neighbors else 'none'}"
        )
    return "\n".join(lines)


def _render_zone_state(result: Mapping[str, Any]) -> str:
    resources_by_zone = {
        row.get("zone_id"): row
        for row in result.get("base_resources") or []
        if isinstance(row, Mapping)
    }
    lines = ["Zone state"]
    zones = result.get("zones")
    if not isinstance(zones, Sequence) or isinstance(zones, (str, bytes)) or not zones:
        lines.append("No matching zones were returned.")
    else:
        for zone in zones:
            if not isinstance(zone, Mapping):
                continue
            zone_id = zone.get("zone_id")
            lines.append(
                f"- {_display_value(zone_id)}: role={_display_value(zone.get('zone_role'))}; "
                f"owner={_display_value(zone.get('known_owner'))}; "
                f"vision={_display_value(zone.get('vision_state'))}; "
                "visible enemy weapon in range="
                f"{_display_value(zone.get('visible_enemy_weapon_in_range'))}"
            )
            own = zone.get("own_contents") if isinstance(zone.get("own_contents"), Mapping) else {}
            visible = zone.get("visible_enemy_contents") if isinstance(zone.get("visible_enemy_contents"), Mapping) else {}
            last_seen = zone.get("last_seen_enemy_contents") if isinstance(zone.get("last_seen_enemy_contents"), Mapping) else {}
            lines.append(
                f"  Own: units={_display_counts(own.get('units'))}; "
                f"buildings={_display_counts(own.get('buildings'))}"
            )
            lines.append(
                f"  Visible enemy: units={_display_counts(visible.get('units'))}; "
                f"buildings={_display_counts(visible.get('buildings'))}"
            )
            lines.append(
                f"  Last seen enemy: units={_display_counts(last_seen.get('units'))}; "
                f"buildings={_display_counts(last_seen.get('buildings'))}; "
                f"age={_display_value(zone.get('enemy_information_age_seconds'))}"
            )
            resource = resources_by_zone.get(zone_id)
            if isinstance(resource, Mapping):
                lines.append(
                    "  Resources: "
                    f"minerals={_display_value(resource.get('minerals_remaining'))}/"
                    f"{_display_value(resource.get('minerals_initial'))}; "
                    f"gas={_display_value(resource.get('vespene_remaining'))}/"
                    f"{_display_value(resource.get('vespene_initial'))}; "
                    f"gas structures={_display_value(resource.get('owned_gas_structure_count'))}; "
                    f"geysers available={_display_value(resource.get('available_geyser_slots'))}/"
                    f"{_display_value(resource.get('geyser_slots'))}; "
                    f"visibility={_display_value(resource.get('resource_visibility'))}"
                )
    unknown = result.get("unknown_zone_ids")
    if isinstance(unknown, Sequence) and not isinstance(unknown, (str, bytes)) and unknown:
        lines.append(f"Unknown zone IDs: {_display_list(unknown)}")
    return "\n".join(lines)


def render_tool_result(name: str, result: Any) -> str:
    """Render provider-facing tool output as compact, labeled text."""
    if not isinstance(result, Mapping):
        return f"Tool result for {name}\n{_display_value(result)}"
    if result.get("error") and name not in {
        "query_unit_data", "query_building_data", "query_research_data",
        "query_prerequisite_path",
    }:
        return f"Tool error for {name}: {_display_value(result.get('error'))}"
    if name == "query_map_overview":
        return _render_map_overview(result)
    if name == "query_zone_state":
        return _render_zone_state(result)
    if name == "query_route":
        segments = []
        for segment in result.get("segments") or []:
            if isinstance(segment, Mapping):
                segments.append(
                    f"{_display_value(segment.get('from'))} -> "
                    f"{_display_value(segment.get('to'))} "
                    f"({_display_value(segment.get('distance'))})"
                )
        return "\n".join((
            f"Route from {_display_value(result.get('from_zone'))} to {_display_value(result.get('to_zone'))}",
            f"Path: {' -> '.join(str(item) for item in result.get('path') or []) or 'none'}",
            f"Segments: {'; '.join(segments) if segments else 'none'}",
            f"Total path distance: {_display_value(result.get('total_distance'))}",
        ))
    if name == "query_unit_data":
        return _render_catalog_results(result, kind="unit")
    if name == "query_building_data":
        return _render_catalog_results(result, kind="building")
    if name == "query_research_data":
        return _render_catalog_results(result, kind="research")
    if name == "query_prerequisite_path":
        lines = [
            f"Prerequisite path for {_display_value(result.get('target'))}",
            "Static dependency path, not a match build order.",
        ]
        if result.get("error"):
            lines.append(f"Error: {_display_value(result.get('error'))}")
            if result.get("candidates"):
                lines.append(f"Close candidates: {_display_list(result.get('candidates'))}")
            return "\n".join(lines)
        lines.append("Step | Type | Target | Minerals | Gas | Time | Requires")
        for step in result.get("steps") or []:
            if not isinstance(step, Mapping):
                continue
            lines.append(
                f"{_display_value(step.get('step'))} | {_display_value(step.get('type'))} | "
                f"{_display_value(step.get('target'))} | {_display_value(step.get('minerals'))} | "
                f"{_display_value(step.get('gas'))} | {_display_value(step.get('time_seconds'))} | "
                f"{_display_list(step.get('requires'))}"
            )
        return "\n".join(lines)
    if name == "query_race_data":
        upgrades = result.get("upgrades") if isinstance(result.get("upgrades"), Mapping) else {}
        observable = result.get("observable_only") if isinstance(result.get("observable_only"), Mapping) else {}
        return "\n".join((
            f"{_display_value(result.get('race')).title()} catalog",
            f"Controllable units: {_display_list(result.get('units'))}",
            f"Controllable buildings: {_display_list(result.get('buildings'))}",
            f"Research: {_display_list(upgrades.get('research'))}",
            f"Structure morphs: {_display_list(upgrades.get('structure'))}",
            f"Observable-only units: {_display_list(observable.get('units'))}",
            f"Observable-only buildings: {_display_list(observable.get('buildings'))}",
            f"Platform abilities: {_display_list(result.get('platform_abilities'))}",
        ))
    if result.get("status") == "queued" and isinstance(result.get("call"), Mapping):
        call = result["call"]
        arguments = call.get("arguments") if isinstance(call.get("arguments"), Mapping) else {}
        rendered_args = ", ".join(f"{key}={value}" for key, value in arguments.items())
        return f"Action queued: {_display_value(call.get('name'))}({rendered_args})"
    return f"Tool result for {name}: {_display_value(result)}"

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


def _schema(name: str, description: str, properties: Dict[str, Any], required: Sequence[str] = ()) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


def action_tool_names(race: str = "terran") -> Tuple[str, ...]:
    """Shared tools, plus race abilities that this catalog actually defines."""
    from sc2bench_env.interface.catalogs import get_catalog

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


def _action_tool_schemas(race: str = "terran") -> List[Dict[str, Any]]:
    from sc2bench_env.interface.decision_rules import action_tool_argument_schema

    schemas = []
    for name in action_tool_names(race):
        properties, required = action_tool_argument_schema(name)
        schemas.append(_schema(name, _action_description(name, race), properties, required))
    return schemas


def tool_schemas(race: str = "terran") -> List[Dict[str, Any]]:
    """Compact OpenAI-style function tools; no catalog enums in descriptions."""
    return [
        _schema(
            "query_map_overview",
            "Use when selecting zones, targets or routes without verified map topology.",
            {},
        ),
        _schema(
            "query_zone_state",
            "Use when a decision depends on the current or last-seen state of specific zones.",
            {"zone_ids": {"type": "array", "items": {"type": "string"}}},
            ("zone_ids",),
        ),
        _schema(
            "query_route",
            "Use when movement or target selection depends on the route or distance between two zones. Does not reveal hidden enemies.",
            {"from_zone": {"type": "string"}, "to_zone": {"type": "string"}},
            ("from_zone", "to_zone"),
        ),
        _schema(
            "query_unit_data",
            "Get cost, supply, production, prerequisites, roles, movement and available durability and weapon data for the named units.",
            {"race": _RACE_PROPERTY, "names": {"type": "array", "items": {"type": "string"}}},
            ("race", "names"),
        ),
        _schema(
            "query_building_data",
            "Get cost, build time, builder, prerequisites, supply provided, roles and available weapon data for the named buildings.",
            {"race": _RACE_PROPERTY, "names": {"type": "array", "items": {"type": "string"}}},
            ("race", "names"),
        ),
        _schema(
            "query_research_data",
            "Get cost, duration, facility, prerequisites and recorded effects for the named research items.",
            {"race": _RACE_PROPERTY, "names": {"type": "array", "items": {"type": "string"}}},
            ("race", "names"),
        ),
        _schema(
            "query_prerequisite_path",
            "Get the static technology dependency path for one target. This is not a match build order.",
            {"race": _RACE_PROPERTY, "target": {"type": "string"}},
            ("race", "target"),
        ),
        _schema(
            "query_race_data",
            "List the canonical controllable and observable units, buildings, research items, structure morphs and platform abilities for one race.",
            {"race": _RACE_PROPERTY},
            ("race",),
        ),
        *_action_tool_schemas(race),
    ]


def _spec_payload(spec, *, kind: str) -> Dict[str, Any]:
    payload = {
        "name": spec.name,
        "minerals": spec.minerals,
        "vespene": spec.vespene,
        "time_seconds": spec.base_time_seconds,
        "prerequisites": list(spec.prerequisites),
        "description": spec.description,
    }
    if kind == "unit":
        payload.update({"supply": spec.supply, "produced_at": spec.produced_at})
    elif kind == "building":
        payload.update({"builder": spec.produced_at or "scv", "kind": spec.kind})
    elif kind == "research":
        payload.update({"facility": spec.produced_at})
    return payload


def _lookup(name: str, *, race: str, expected_actions: Sequence[str], kind: str) -> Dict[str, Any]:
    from sc2bench_env.data.knowledge import attach, close_names, observable_payload

    spec = get_target(name, race=race)
    if spec is None:
        observed = observable_payload(race, name, kind)
        if observed is not None:
            return observed
        return {
            "name": name,
            "error": "unknown_target",
            "candidates": close_names(race, name, kind),
        }
    if spec.action not in expected_actions:
        return {"name": spec.name, "error": f"not_a_{kind}_target", "action": spec.action}
    payload = _spec_payload(spec, kind=kind)
    if spec.morph_from:
        payload["morph_from"] = spec.morph_from
    if kind == "research":
        payload["availability"] = "controllable"
        return payload
    return attach(payload, race, spec.name)


def query_unit_data(names: Sequence[str], *, race: str = "terran") -> Dict[str, Any]:
    return {"results": [_lookup(name, race=race, expected_actions=("train",), kind="unit") for name in names]}


def query_building_data(names: Sequence[str], *, race: str = "terran") -> Dict[str, Any]:
    return {
        "results": [
            _lookup(name, race=race, expected_actions=("build", "upgrade"), kind="building")
            for name in names
        ]
    }


def query_research_data(names: Sequence[str], *, race: str = "terran") -> Dict[str, Any]:
    return {
        "results": [_lookup(name, race=race, expected_actions=("research",), kind="research") for name in names]
    }


def query_prerequisite_path(target: str, *, race: str = "terran") -> Dict[str, Any]:
    from sc2bench_env.data.knowledge import close_names

    spec = get_target(target, race=race)
    if spec is None:
        return {
            "target": target,
            "error": "unknown_target",
            "candidates": list(dict.fromkeys(
                close_names(race, target, "unit")
                + close_names(race, target, "building")
                + close_names(race, target, "research")
            )),
        }
    catalog = {item.name: get_target(item.name, race=race) for item in get_catalog(race=race).targets}
    ordered: List[str] = []
    seen: set[str] = set()

    def walk(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        item = catalog.get(name)
        if item is None:
            ordered.append(name)
            return
        for req in item.prerequisites:
            walk(req)
        if name not in ordered:
            ordered.append(name)

    walk(spec.name)
    steps = []
    for index, name in enumerate(ordered, start=1):
        item = catalog.get(name)
        steps.append({
            "step": index,
            "type": None if item is None else item.action,
            "target": name,
            "minerals": None if item is None else item.minerals,
            "gas": None if item is None else item.vespene,
            "time_seconds": None if item is None else item.base_time_seconds,
            "requires": [] if item is None else list(item.prerequisites),
        })
    return {
        "target": spec.name,
        "static_dependency_path": True,
        "note": "Static dependency path, not a match build order.",
        "path": ordered,
        "steps": steps,
    }


def _require_knowledge_race(race: Any) -> str:
    if race not in _KNOWLEDGE_RACES:
        raise ValueError("race must be terran, protoss or zerg")
    return str(race)


def query_race_data(race: str) -> Dict[str, Any]:
    from sc2bench_env.data.knowledge import observable_groups

    require_supported_own_race(race)
    catalog = get_catalog(race=race)
    units, buildings, research, structure = [], [], [], []
    for spec in catalog.targets:
        if spec.action == "train":
            units.append(spec.name)
        elif spec.action == "build":
            buildings.append(spec.name)
        elif spec.action == "research":
            research.append(spec.name)
        elif spec.action == "upgrade":
            structure.append(spec.name)
    return {
        "race": race,
        "units": units,
        "buildings": buildings,
        "upgrades": {"research": research, "structure": structure},
        "observable_only": observable_groups(race),
        "platform_abilities": [
            spec.name for spec in catalog.targets
            if spec.action in {"scan", "call_mule", "chrono_boost", "inject_larva", "spawn_creep_tumor", "scout"}
        ],
    }


def query_map_overview(observation: Mapping[str, Any]) -> Dict[str, Any]:
    topology = dict(observation.get("map_topology") or {})
    return {"map_topology": topology}


def query_zone_state(observation: Mapping[str, Any], zone_ids: Sequence[str]) -> Dict[str, Any]:
    wanted = [str(zone) for zone in zone_ids]
    rows = [
        row for row in observation.get("zone_state") or []
        if isinstance(row, Mapping) and row.get("zone_id") in set(wanted)
    ]
    resources = [
        row for row in (observation.get("map_control") or {}).get("base_resources") or []
        if isinstance(row, Mapping) and row.get("zone_id") in set(wanted)
    ]
    missing = [zone for zone in wanted if zone not in {row.get("zone_id") for row in rows}]
    return {"zones": rows, "base_resources": resources, "unknown_zone_ids": missing}


def query_route(observation: Mapping[str, Any], from_zone: str, to_zone: str) -> Dict[str, Any]:
    topology = observation.get("map_topology") or {}
    rows = {row.get("zone_id"): row for row in topology.get("zones") or [] if isinstance(row, Mapping)}
    if from_zone not in rows or to_zone not in rows:
        return {"from_zone": from_zone, "to_zone": to_zone, "error": "unknown_zone"}
    if from_zone == to_zone:
        return {"from_zone": from_zone, "to_zone": to_zone, "path": [from_zone], "segments": [], "total_distance": 0}
    neighbors = {
        name: [item["zone_id"] for item in (row.get("corridor_neighbors") or []) if isinstance(item, Mapping)]
        for name, row in rows.items()
    }
    distances = {}
    for name, row in rows.items():
        for item in row.get("corridor_neighbors") or []:
            if isinstance(item, Mapping):
                distances[(name, item["zone_id"])] = item.get("path_distance")
    queue = [(from_zone, [from_zone])]
    seen = {from_zone}
    while queue:
        current, path = queue.pop(0)
        for neighbor in neighbors.get(current, []):
            if neighbor in seen:
                continue
            next_path = path + [neighbor]
            if neighbor == to_zone:
                segments = [
                    {"from": next_path[i], "to": next_path[i + 1],
                     "distance": distances.get((next_path[i], next_path[i + 1]))}
                    for i in range(len(next_path) - 1)
                ]
                total = 0.0
                known = True
                for segment in segments:
                    if not isinstance(segment["distance"], (int, float)):
                        known = False
                        break
                    total += float(segment["distance"])
                return {
                    "from_zone": from_zone, "to_zone": to_zone, "path": next_path,
                    "segments": segments, "total_distance": total if known else None,
                }
            seen.add(neighbor)
            queue.append((neighbor, next_path))
    return {"from_zone": from_zone, "to_zone": to_zone, "error": "no_corridor_path"}


def execute_tool(
    name: str,
    arguments: Optional[Mapping[str, Any]] = None,
    *,
    observation: Optional[Mapping[str, Any]] = None,
    race: str = "terran",
) -> Dict[str, Any]:
    arguments = dict(arguments or {})
    if name in ACTION_TOOLS:
        return queued_tool_result(NormalizedToolCall(name, arguments))
    observation = observation or {}
    try:
        if name == "query_map_overview":
            return query_map_overview(observation)
        if name == "query_zone_state":
            ids = arguments.get("zone_ids")
            if not isinstance(ids, list):
                return {"error": "zone_ids must be an array of strings"}
            return query_zone_state(observation, ids)
        if name == "query_route":
            return query_route(observation, str(arguments.get("from_zone") or ""), str(arguments.get("to_zone") or ""))
        if name in KNOWLEDGE_TOOLS:
            selected = _require_knowledge_race(arguments.get("race"))
            if name == "query_unit_data":
                return query_unit_data(list(arguments.get("names") or []), race=selected)
            if name == "query_building_data":
                return query_building_data(list(arguments.get("names") or []), race=selected)
            if name == "query_research_data":
                return query_research_data(list(arguments.get("names") or []), race=selected)
            if name == "query_prerequisite_path":
                return query_prerequisite_path(str(arguments.get("target") or ""), race=selected)
            return query_race_data(selected)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"error": f"unknown_tool {name}"}
