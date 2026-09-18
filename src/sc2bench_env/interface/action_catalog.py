"""Single-source Action Catalog for SC2Bench.

Defines legal targets, costs, prerequisites, success boundaries, and
cross-round semantics. Downstream consumers:

- model-facing catalog text / system prompt
- JSON Schema fragments for decision validation
- FakeBackend costs and prerequisites
- known-target checks in the parser
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from sc2bench_env.interface.races import require_supported_own_race
from sc2bench_env.interface.catalog_types import TargetSpec
from sc2bench_env.interface.catalogs import get_catalog
from sc2bench_env.interface.catalogs.terran import TERRAN_TARGETS

from sc2bench_env.interface.platform_rules import (
    ACTION_RULES, ARMY_RULES, COMBAT_DISPATCH_RULES, COMBAT_RETARGET_RULES,
    COMBAT_STYLE_RULES, CONTROL_RULES, GAME_RULES, INTERACTION_RULES,
    PLANNING_RULES, ROLE_RULES, WAIT_MEANINGS,
)


COMBAT_STYLES: Tuple[str, ...] = ("attack", "defend")


ACTION_VERBS: Tuple[str, ...] = (
    "build",
    "train",
    "research",
    "cancel",
    "scan",
    "call_mule",
    "scout",
    "upgrade",
    "combat",
    "retreat",
    "wait",
)

WAIT_CONDITIONS: Tuple[str, ...] = (
    "interval",
    "resource_at_least",
    "supply_left_at_most",
    "unit_count_at_least",
    "building_count_at_least",
    "scan_ready",
    "game_time_at_least",
    "zone_under_attack",
)


@lru_cache(maxsize=None)
def _catalog_indexes(race: str) -> Tuple[Mapping[str, TargetSpec], Mapping[str, Tuple[TargetSpec, ...]]]:
    from types import MappingProxyType

    specs = get_catalog(race=race).targets
    validate_catalog(specs)
    by_name = {spec.name: spec for spec in specs}
    by_action: Dict[str, List[TargetSpec]] = {}
    for spec in specs:
        by_action.setdefault(spec.action, []).append(spec)
    return (
        MappingProxyType(by_name),
        MappingProxyType({key: tuple(value) for key, value in by_action.items()}),
    )


def get_target(name: str, *, race: str = "terran") -> Optional[TargetSpec]:
    return _catalog_indexes(race)[0].get(str(name or "").strip().lower())


def targets_for_action(action: str, *, race: str = "terran") -> Tuple[TargetSpec, ...]:
    return _catalog_indexes(race)[1].get(action, ())


def known_target_names(action: Optional[str] = None, *, race: str = "terran") -> Tuple[str, ...]:
    if action is None:
        return tuple(sorted(_catalog_indexes(race)[0]))
    return tuple(sorted(spec.name for spec in targets_for_action(action, race=race)))


def cost_table(*, race: str = "terran") -> Dict[str, Dict[str, int]]:
    return {spec.name: spec.cost_dict() for spec in get_catalog(race=race).targets}


def prerequisite_table(*, race: str = "terran") -> Dict[str, List[str]]:
    return {
        spec.name: list(spec.prerequisites)
        for spec in get_catalog(race=race).targets
        if spec.prerequisites
    }


def validate_catalog(specs: Iterable[TargetSpec] = TERRAN_TARGETS) -> None:
    """Fail fast on duplicate names or unknown prerequisite references."""
    specs = tuple(specs)
    seen = set()
    names = {spec.name for spec in specs}
    for spec in specs:
        if spec.name in seen:
            raise ValueError(f"duplicate catalog target {spec.name!r}")
        seen.add(spec.name)
        if spec.action not in ACTION_VERBS:
            raise ValueError(f"{spec.name}: unknown action {spec.action!r}")
        for req in spec.prerequisites:
            if req not in names:
                raise ValueError(f"{spec.name}: unknown prerequisite {req!r}")


def _target_table_lines(action: str, *, race: str = "terran") -> List[str]:
    """Same metadata rows for standalone catalog and action-local prompt tables."""
    specs = targets_for_action(action, race=race)
    groups = {}
    for spec in specs:
        facility = spec.produced_at if action in {"train", "research"} else spec.morph_from if action == "upgrade" else ""
        groups.setdefault(facility, []).append(spec)
    if action in {"build", "train", "research", "upgrade"}:
        time_column = {
            "build": "build time (s)", "train": "train time (s)",
            "research": "research time (s)", "upgrade": "morph time (s)",
        }[action]
        columns = ["name", "cost minerals/vespene"]
        if action == "train":
            columns.append("supply")
        columns += [time_column, "additional prerequisites"]
    elif action in {"scan", "call_mule"}:
        columns = ["name", "energy", "prerequisites"]
    else:
        columns = ["name", "prerequisites"]
    lines = [" | ".join(columns)]
    for facility, members in groups.items():
        if facility:
            lines.append(f"facility={facility}")
        for spec in members:
            prerequisites = [name for name in spec.prerequisites if name != facility]
            values = {
                "name": spec.name, "cost minerals/vespene": f"{spec.minerals}/{spec.vespene}",
                "supply": str(spec.supply), "energy": str(spec.energy),
                "additional prerequisites": ", ".join(prerequisites) or "none",
                "prerequisites": ", ".join(spec.prerequisites) or "none",
            }
            if action in {"build", "train", "research", "upgrade"}:
                values[time_column] = f"{spec.base_time_seconds:g}"
            lines.append(" | ".join(values[column] for column in columns))
    return lines


def render_action_catalog(*, race: str = "terran") -> str:
    """Standalone reference API; the model prompt embeds tables beside actions."""
    require_supported_own_race(race)
    lines = [f"Action Catalog ({race}):", *get_catalog(race=race).table_legend, ""]
    for action in ("build", "train", "research", "upgrade", "scan", "call_mule", "scout"):
        lines += [f"[{action}]", *_target_table_lines(action, race=race), ""]
    lines += ["Target notes:", *(note for notes in get_catalog(race=race).target_notes.values() for note in notes)]
    return "\n".join(lines) + "\n"


def render_system_prompt(*, race: str = "terran") -> str:
    require_supported_own_race(race)
    sections = (
        ("1. Role and objective", ROLE_RULES + "\n" + CONTROL_RULES + "\n" + PLANNING_RULES),
        ("2. Interaction protocol", INTERACTION_RULES),
        ("3. Reading Observation", render_observation_guide()),
        ("4. Actions", render_decision_guide(race=race)),
        ("5. Game basics", GAME_RULES),
    )
    return "\n\n".join(f"{heading}\n{body.rstrip()}" for heading, body in sections) + "\n"


def decision_examples(*, race: str = "terran") -> Tuple[List[Dict[str, Any]], ...]:
    """Developer/Schema examples; never injected into the fixed model prompt."""
    require_supported_own_race(race)
    return (
        [
            {"action": "build", "target": "supply_depot"},
            {"action": "research", "target": "concussive_shells"},
            {"action": "train", "target": "scv", "count": 2},
            {"action": "wait", "any_of": [
                {"condition": "interval", "seconds": 20},
                {"condition": "supply_left_at_most", "amount": 2},
            ]},
        ],
        [
            {"action": "upgrade", "target": "cc_0", "to": "orbital_command"},
            {"action": "scout", "route": ["zone_1", "zone_2"]},
            {"action": "scan", "target": "zone_3"},
            {"action": "call_mule"},
            {"action": "train", "target": "medivac", "count": 2},
            {"action": "wait", "all_of": [
                {"condition": "interval", "seconds": 10},
                {"condition": "resource_at_least", "resource": "minerals", "amount": 400},
            ]},
        ],
        [
            {"action": "cancel", "target_action": "train", "target": "marine"},
            {"action": "combat", "style": "attack", "target": "zone_3",
             "units": {"marine": 16, "marauder": 4, "medivac": 2}},
            {"action": "wait", "any_of": [
                {"condition": "interval", "seconds": 15},
                {"condition": "zone_under_attack", "zone": "zone_0"},
            ]},
        ],
        [
            {"action": "train", "target": "siege_tank", "count": 2},
            {"action": "combat", "group": "group_1", "style": "attack", "target": "zone_10"},
            {"action": "retreat", "group": "group_2"},
            {"action": "wait", "any_of": [{"condition": "interval", "seconds": 5}]},
        ],
    )


def action_syntax_examples() -> Tuple[Dict[str, Any], ...]:
    """Entry templates derived from shared Schema examples, not a build order."""
    entries = []
    seen = set()
    for batch in decision_examples():
        for entry in batch[:-1]:
            encoded = json.dumps(entry, sort_keys=True)
            if encoded not in seen:
                entries.append(entry)
                seen.add(encoded)
    entries += [{"action": "wait"}, decision_examples()[0][-1], decision_examples()[1][-1]]
    return tuple(entries)


def wait_condition_examples() -> Tuple[Dict[str, Any], ...]:
    """Concrete condition syntax; checked against actual field/value validators."""
    return (
        {"condition": "interval", "seconds": 20},
        {"condition": "resource_at_least", "resource": "minerals", "amount": 400},
        {"condition": "supply_left_at_most", "amount": 2},
        {"condition": "unit_count_at_least", "unit": "medivac", "count": 2},
        {"condition": "building_count_at_least", "building": "starport", "count": 1},
        {"condition": "scan_ready", "count": 1},
        {"condition": "game_time_at_least", "seconds": 300},
        {"condition": "zone_under_attack", "zone": "zone_0"},
    )


def render_decision_guide(*, race: str = "terran") -> str:
    """Action-local syntax, semantics and complete catalog-derived metadata."""
    require_supported_own_race(race)
    from sc2bench_env.interface.decision_rules import VERB_FIELD_RULES, WAIT_CONDITION_RULES
    import json
    import re
    from sc2bench_env.interface.prompt_fields import ACTION_FIELDS, WAIT_FIELDS, FORMAT_HEADER, COMMAND_TEMPLATES

    if set(ACTION_RULES) != set(VERB_FIELD_RULES) or set(ACTION_FIELDS) != set(VERB_FIELD_RULES):
        raise RuntimeError("Prompt descriptions must cover the actual actions")
    if set(WAIT_MEANINGS) != set(WAIT_CONDITION_RULES) or set(WAIT_FIELDS) != set(WAIT_CONDITION_RULES):
        raise RuntimeError("Prompt descriptions must cover all wait conditions")
    for verb, rule in VERB_FIELD_RULES.items():
        if set(ACTION_FIELDS[verb]) != rule.allowed - {"action"}:
            raise RuntimeError("Prompt descriptions must cover the allowed action fields")
    for name, rule in WAIT_CONDITION_RULES.items():
        if set(WAIT_FIELDS[name]) != rule.allowed_params:
            raise RuntimeError("Prompt descriptions must cover the allowed condition fields")

    # Check the rendered forms themselves, not only a parallel description table.
    expected_forms = (set(VERB_FIELD_RULES) - {"wait", "combat"}) | {"combat_units", "combat_group"}
    if set(COMMAND_TEMPLATES) != expected_forms:
        raise RuntimeError("Prompt templates must cover the actual action forms")
    rendered_fields = {}
    for form, template in COMMAND_TEMPLATES.items():
        entry = json.loads(re.sub(r"(?<!\")<positive_integer>", "1", template))
        rule = VERB_FIELD_RULES[entry["action"]]
        required = set(rule.required) | {"action"}
        if not required <= set(entry) <= rule.allowed:
            raise RuntimeError("Prompt templates must use the allowed action fields")
        if entry["action"] == "combat" and ("units" in entry) == ("group" in entry):
            raise RuntimeError("Prompt combat templates must select units or group")
        expected_verb = "combat" if form.startswith("combat_") else form
        if entry["action"] != expected_verb:
            raise RuntimeError("Prompt templates must match their action form")
        if form in {"combat_units", "combat_group"} and form.split("_", 1)[1] not in entry:
            raise RuntimeError("Prompt combat templates must match their action form")
        rendered_fields.setdefault(entry["action"], set()).update(entry)
    for verb, rule in VERB_FIELD_RULES.items():
        if verb != "wait" and rendered_fields.get(verb) != rule.allowed:
            raise RuntimeError("Prompt templates must cover the allowed action fields")

    lines = [
        "Action reference:",
        "Use exactly the fields in each form below; no action_id or extra keys. Only wait.any_of/all_of and marked condition parameters may be omitted.",
        "train.count/combat.units use positive JSON integers, not strings or booleans. route is nonempty; units has one or more unit/count pairs.",
        "Zone/group IDs are quoted JSON strings zone_<index>/group_<index>, copied exactly from Observation; never numeric indices.",
        f"Target tables ({race}):",
        *get_catalog(race=race).table_legend,
        FORMAT_HEADER.rstrip(),
        "",
        "Production and construction:",
    ]
    for verb in ("build", "train", "research", "upgrade", "cancel"):
        lines += [verb, COMMAND_TEMPLATES[verb], ACTION_RULES[verb]]
        if targets_for_action(verb, race=race):
            lines += _target_table_lines(verb, race=race)
        lines += [*get_catalog(race=race).target_notes.get(verb, ()), ""]
    lines += [
        "Army control:",
        ARMY_RULES.rstrip(),
        ACTION_RULES["combat"],
        "",
        "Dispatch a new group:",
        COMMAND_TEMPLATES["combat_units"],
        COMBAT_DISPATCH_RULES,
        "",
        "Retarget an existing group:",
        COMMAND_TEMPLATES["combat_group"],
        COMBAT_RETARGET_RULES,
        "",
        "Return a group home:",
        COMMAND_TEMPLATES["retreat"],
        ACTION_RULES["retreat"],
        "",
        "Combat styles:",
        COMBAT_STYLE_RULES.rstrip(),
        "",
        "Reconnaissance and abilities:",
    ]
    for verb in ("scout", "scan", "call_mule"):
        lines += [verb, COMMAND_TEMPLATES[verb], ACTION_RULES[verb], *_target_table_lines(verb, race=race), ""]
    lines += [
        "Wait:",
        "action: wait",
        "any_of/all_of: optional JSON arrays of the condition objects below.",
        ACTION_RULES["wait"],
        "wait has no seconds or interval field. Nest condition=interval and seconds inside any_of/all_of. Do not put seconds directly on wait.",
        "any_of is OR; all_of is AND. With both: (any_of OR) AND (all_of AND). An interval in any_of does not bypass all_of.",
        "Conditions test current state, not only new events; already-true conditions may return immediately.",
        "Every step returns after at most 60 game seconds or on termination, overriding unmet conditions/long intervals. This does not terminate the episode or cancel tasks.",
        "",
        "Condition objects:",
        "Only the eight condition names below are supported; do not invent inverses or new names. Each sets condition and only listed fields, optional if marked may omit.",
    ]
    for name, rule in WAIT_CONDITION_RULES.items():
        params = [
            f"{field}: {WAIT_FIELDS[name][field]}" + (" (may omit)" if field in rule.optional else "")
            for field in (*rule.required, *rule.optional)
        ]
        lines += [name, "Fields: " + "; ".join(params), WAIT_MEANINGS[name]]
    return "\n".join(lines) + "\n"


def render_observation_guide() -> str:
    return "\n".join([
        "Reading Observation and Feedback:",
        "- unknown means unavailable, not zero; none means an empty collection. Names/IDs retain their exact platform spelling.",
        "- Game uses game seconds; unidentified Random race stays unknown.",
        "- Economy: current minerals/vespene; supply occupied/capacity/free. Workers=living SCV/Probe/Drone including cargo, excluding MULEs and unfinished training. Income=game collection-rate score per minute. ideal workers=mining capacity, not a recommended production target.",
        "- Zone State: zone_role=location, not current ownership; known_owner=self/enemy/unconfirmed, never calls fogged territory neutral. vision_state=center visibility, not the whole zone.",
        "- Contents: OWN=your units/buildings, even at enemy-base locations. ENEMY visible=current vision; ENEMY last seen=history. Age=oldest represented sighting. Fog of war is partial information. Multi-spawn enemy-base roles remain unconfirmed until identified.",
        "- Map Topology: terrain ground distances, not travel times; corridor links do not guarantee adjacency. Missing distances/links are unknown, not unreachable. Sharpy handles pathfinding.",
        "- Base Resources: remaining/initial; unknown initial is not guessed. Fog quantities=last confirmed. Geyser slots=map nodes; owned gas structures include unfinished ones; free slots may be unknown.",
        "- Buildings: Ready=living completed; Under construction=unfinished entity; Worker en route=dispatched; Waiting to start=unassigned. Structures supplies upgrade IDs.",
        "- Training: Paid training queue=purchased unfinished units; Additional units waiting=accepted, not queued. Neither is living inventory (Own Forces).",
        "- Production Priority: real cross-round priority order, one row/request. Remaining=unproduced/unstarted; Order progress=births/requested, not living inventory. Paid/Unqueued=purchased/unpurchased. Cancellable=quantity cancel can remove from this row (unknown stays unknown). State/Waiting for=execution; not reported is not readiness. Terminal rows disappear.",
        "- Production Capacity: ready grounded hosts/attached add-ons; orders occupy slots. Free Tech Lab slots are a subset of shared/unreserved free slots, not extra capacity. Accepted training repeats Training totals by producer type, not assignments to individual buildings. Unit technology groups units with identical prerequisite status: tech ready does not mean affordable or an available slot, and is not a recommendation. Unsupported backends=unknown.",
        "- Waiting reasons count waiting work, not living/paid units. Budget includes earlier priority; paid queues continue. Refinery with no eligible free geyser reserves no budget and is rechecked each frame.",
        "- Research: waiting_to_start/waiting_for/in_progress/completed; completed=finished upgrade, not accepted/queued.",
        "- Own Forces: living inventory; free=dispatchable home pool; assigned=unavailable including reservations. Cargo keeps its unit name.",
        "- Terran Abilities: per-Orbital energy and ready scan/MULE counts; energies cannot be pooled.",
        "- Army Groups: group_0=home defense; requested=original composition; alive=survivors. nearest_zone=closest to mean on-map position, not arrival. executing=running order, not evidence of fighting/clearance.",
        "- Transport: loaded=current passengers; activity=control branch, not success. Peak loaded/drop_unloaded=history, not current cargo.",
        "- visible_enemy_nearby counts current visible enemies within 15 of on-map members, not memory/snapshots. weapon_cooldown_active_count=recent weapon activity, not proof of ongoing fighting, damage or kills. Zero nearby does not prove zone clearance.",
        "- visible_enemy_weapon_in_range means a currently visible enemy weapon can reach own units/buildings; zone_under_attack uses this, not measured damage.",
        "- Recent Events=bounded updates, not full history. Feedback=previous submission; Observation=current execution. Duplicate events omitted; name normalizations=spelling conversions.",
    ]) + "\n"


def decision_json_schema(*, race: str = "terran") -> Dict[str, Any]:
    """Platform-owned JSON Schema for one decision array (draft-07 style)."""
    from sc2bench_env.interface.decision_rules import (
        decision_json_schema as _decision_json_schema,
    )

    return _decision_json_schema(race=race)


def catalog_as_dicts(*, race: str = "terran") -> List[Dict[str, Any]]:
    return [spec.to_dict() for spec in get_catalog(race=race).targets]


# Import-time consistency check.
validate_catalog()
