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
    COMBAT_STYLE_RULES, CONTROL_RULES, EXECUTION_RULES, GAME_RULES, INTERACTION_RULES,
    PROTOSS_CONTROL_RULES, PROTOSS_GAME_RULES, PROTOSS_ROLE_RULES,
    ZERG_CONTROL_RULES, ZERG_GAME_RULES, ZERG_ROLE_RULES,
    PLANNING_RULES, ROLE_RULES,
)


COMBAT_STYLES: Tuple[str, ...] = ("attack", "defend")


ACTION_VERBS: Tuple[str, ...] = (
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
    "advance",
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


def _numbered(spec: TargetSpec, race: str) -> TargetSpec:
    from sc2bench_env.data.knowledge import apply_action_numbers

    return apply_action_numbers(spec, race)


def get_target(name: str, *, race: str = "terran") -> Optional[TargetSpec]:
    spec = _catalog_indexes(race)[0].get(str(name or "").strip().lower())
    return None if spec is None else _numbered(spec, race)


def targets_for_action(action: str, *, race: str = "terran") -> Tuple[TargetSpec, ...]:
    return tuple(_numbered(spec, race) for spec in _catalog_indexes(race)[1].get(action, ()))


def known_target_names(action: Optional[str] = None, *, race: str = "terran") -> Tuple[str, ...]:
    if action is None:
        return tuple(sorted(_catalog_indexes(race)[0]))
    return tuple(sorted(spec.name for spec in targets_for_action(action, race=race)))


def cost_table(*, race: str = "terran") -> Dict[str, Dict[str, int]]:
    return {
        spec.name: get_target(spec.name, race=race).cost_dict()
        for spec in get_catalog(race=race).targets
    }


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
    """Render one explicit row per target; no inherited facility context."""
    specs = targets_for_action(action, race=race)
    columns_by_action = {
        "build": ("name", "M/G", "time", "builder", "extra", "use"),
        "train": ("name", "M/G", "supply", "time", "producer", "extra", "use"),
        "research": ("name", "M/G", "time", "facility", "extra", "use"),
        "upgrade": ("name", "M/G", "time", "source", "extra", "use"),
        "scan": ("name", "energy", "source", "extra", "use"),
        "call_mule": ("name", "energy", "source", "extra", "use"),
        "chrono_boost": ("name", "energy", "source", "extra", "use"),
        "inject_larva": ("name", "energy", "source", "extra", "use"),
        "spawn_creep_tumor": ("name", "energy", "source", "extra", "use"),
        "scout": ("name", "unit", "extra", "use"),
    }
    columns = columns_by_action.get(action, ("name", "requires", "use"))
    lines = [" | ".join(columns)]
    worker = {"protoss": "probe", "zerg": "drone"}.get(race, "scv")
    for spec in specs:
        builder = spec.produced_at or (worker if action == "build" else "")
        source = spec.morph_from or {
            "scan": "orbital_command", "call_mule": "orbital_command",
            "chrono_boost": "nexus", "inject_larva": "queen", "spawn_creep_tumor": "queen",
        }.get(action, "")
        inherited = {value for value in (builder, spec.produced_at, source,
                                         worker if action == "scout" else "") if value}
        extra = [name for name in spec.prerequisites if name not in inherited]
        values = {
            "name": spec.name,
            "M/G": f"{spec.minerals}/{spec.vespene}",
            "supply": str(spec.supply),
            "energy": str(spec.energy),
            "time": f"{spec.base_time_seconds:g}s",
            "builder": builder,
            "producer": spec.produced_at,
            "facility": spec.produced_at,
            "source": source,
            "unit": worker if action == "scout" else "",
            "extra": ",".join(extra) or "none",
            "use": get_catalog(race=race).prompt_descriptions.get(spec.name, spec.description),
        }
        lines.append(" | ".join(values[column] for column in columns))
    return lines


def render_action_catalog(*, race: str = "terran") -> str:
    """Standalone reference API; the model prompt embeds tables beside actions."""
    require_supported_own_race(race)
    lines = [f"Action Catalog ({race}):", *get_catalog(race=race).table_legend, ""]
    for action in (
        "build", "train", "research", "upgrade", "scan", "call_mule",
        "chrono_boost", "inject_larva", "spawn_creep_tumor", "scout",
    ):
        block = _target_table_lines(action, race=race)
        if len(block) <= 1:
            continue
        lines += [f"[{action}]", *block, ""]
    lines += ["Target notes:", *(note for notes in get_catalog(race=race).target_notes.values() for note in notes)]
    return "\n".join(lines) + "\n"


def render_system_prompt(*, race: str = "terran") -> str:
    require_supported_own_race(race)
    if race == "protoss":
        role = (
            "You are an autonomous StarCraft II agent controlling protoss through SC2Bench.\n"
            + PROTOSS_ROLE_RULES + "\n" + PROTOSS_CONTROL_RULES
        )
        game = PROTOSS_GAME_RULES
    elif race == "zerg":
        role = (
            "You are an autonomous StarCraft II agent controlling zerg through SC2Bench.\n"
            + ZERG_ROLE_RULES + "\n" + ZERG_CONTROL_RULES
        )
        game = ZERG_GAME_RULES
    else:
        role = (
            f"You are an autonomous StarCraft II agent controlling {race} through SC2Bench.\n"
            + ROLE_RULES + "\n" + CONTROL_RULES
        )
        game = GAME_RULES
    sections = (
        ("1. Role and objective", role),
        ("2. Decision process", INTERACTION_RULES),
        ("3. Platform execution model", PLANNING_RULES + "\n" + EXECUTION_RULES + "\n" + ARMY_RULES),
        ("4. Reading Observation", render_observation_guide()),
        ("5. Game basics", game),
    )
    return "\n\n".join(f"{heading}\n{body.rstrip()}" for heading, body in sections) + "\n"


def decision_examples(*, race: str = "terran") -> Tuple[List[Dict[str, Any]], ...]:
    """Developer/Schema examples; never injected into the fixed model prompt."""
    require_supported_own_race(race)
    if race == "zerg":
        return (
            [
                {"name": "build", "arguments": {"target": "spawning_pool"}},
                {"name": "train", "arguments": {"target": "drone", "count": 2}},
                {"name": "train", "arguments": {"target": "overlord", "count": 1}},
                {"name": "advance", "arguments": {"seconds": 20}},
            ],
            [
                {"name": "upgrade", "arguments": {"target": "cc_0", "to": "lair"}},
                {"name": "inject_larva", "arguments": {}},
                {"name": "spawn_creep_tumor", "arguments": {}},
                {"name": "scout", "arguments": {"route": ["zone_1", "zone_2"]}},
                {"name": "train", "arguments": {"target": "zergling", "count": 6}},
                {"name": "advance", "arguments": {"seconds": 10}},
            ],
            [
                {"name": "cancel", "arguments": {"target_action": "train", "target": "roach"}},
                {"name": "combat", "arguments": {"style": "attack", "target": "zone_3",
                 "units": {"zergling": 8, "roach": 4}}},
                {"name": "advance", "arguments": {"seconds": 15}},
            ],
            [
                {"name": "train", "arguments": {"target": "hydralisk", "count": 2}},
                {"name": "combat", "arguments": {"group": "group_1", "style": "attack", "target": "zone_10"}},
                {"name": "retreat", "arguments": {"group": "group_2"}},
                {"name": "advance", "arguments": {"seconds": 5}},
            ],
        )
    if race == "protoss":
        return (
            [
                {"name": "build", "arguments": {"target": "pylon"}},
                {"name": "research", "arguments": {"target": "warp_gate"}},
                {"name": "train", "arguments": {"target": "probe", "count": 2}},
                {"name": "advance", "arguments": {"seconds": 20}},
            ],
            [
                {"name": "scout", "arguments": {"route": ["zone_1", "zone_2"]}},
                {"name": "chrono_boost", "arguments": {}},
                {"name": "train", "arguments": {"target": "stalker", "count": 2}},
                {"name": "advance", "arguments": {"seconds": 10}},
            ],
            [
                {"name": "cancel", "arguments": {"target_action": "train", "target": "zealot"}},
                {"name": "combat", "arguments": {"style": "attack", "target": "zone_3",
                 "units": {"zealot": 8, "stalker": 4}}},
                {"name": "advance", "arguments": {"seconds": 15}},
            ],
            [
                {"name": "train", "arguments": {"target": "immortal", "count": 2}},
                {"name": "combat", "arguments": {"group": "group_1", "style": "attack", "target": "zone_10"}},
                {"name": "retreat", "arguments": {"group": "group_2"}},
                {"name": "advance", "arguments": {"seconds": 5}},
            ],
        )
    return (
        [
            {"name": "build", "arguments": {"target": "supply_depot"}},
            {"name": "research", "arguments": {"target": "concussive_shells"}},
            {"name": "train", "arguments": {"target": "scv", "count": 2}},
            {"name": "advance", "arguments": {"seconds": 20}},
        ],
        [
            {"name": "upgrade", "arguments": {"target": "cc_0", "to": "orbital_command"}},
            {"name": "scout", "arguments": {"route": ["zone_1", "zone_2"]}},
            {"name": "scan", "arguments": {"target": "zone_3"}},
            {"name": "call_mule", "arguments": {}},
            {"name": "train", "arguments": {"target": "medivac", "count": 2}},
            {"name": "advance", "arguments": {"seconds": 10}},
        ],
        [
            {"name": "cancel", "arguments": {"target_action": "train", "target": "marine"}},
            {"name": "combat", "arguments": {"style": "attack", "target": "zone_3",
             "units": {"marine": 16, "marauder": 4, "medivac": 2}}},
            {"name": "advance", "arguments": {"seconds": 15}},
        ],
        [
            {"name": "train", "arguments": {"target": "siege_tank", "count": 2}},
            {"name": "combat", "arguments": {"group": "group_1", "style": "attack", "target": "zone_10"}},
            {"name": "retreat", "arguments": {"group": "group_2"}},
            {"name": "advance", "arguments": {"seconds": 5}},
        ],
    )


def action_syntax_examples() -> Tuple[Dict[str, Any], ...]:
    """Entry templates derived from shared Schema examples, not a build order."""
    entries = []
    seen = set()
    for race in ("terran", "protoss", "zerg"):
        for batch in decision_examples(race=race):
            for entry in batch[:-1]:
                encoded = json.dumps(entry, sort_keys=True)
                if encoded not in seen:
                    entries.append(entry)
                    seen.add(encoded)
    entries += [{"name": "advance", "arguments": {"seconds": 20}}]
    return tuple(entries)


def render_decision_guide(*, race: str = "terran") -> str:
    """Standalone compatibility guide for army and combat semantics."""
    require_supported_own_race(race)
    from sc2bench_env.interface.decision_rules import VERB_FIELD_RULES
    import json
    import re
    from sc2bench_env.interface.prompt_fields import ACTION_FIELDS, COMMAND_TEMPLATES

    if set(ACTION_RULES) != set(VERB_FIELD_RULES) or set(ACTION_FIELDS) != set(VERB_FIELD_RULES):
        raise RuntimeError("Prompt descriptions must cover the actual actions")
    for verb, rule in VERB_FIELD_RULES.items():
        if set(ACTION_FIELDS[verb]) != rule.allowed - {"action"}:
            raise RuntimeError("Prompt descriptions must cover the allowed action fields")

    expected_forms = (set(VERB_FIELD_RULES) - {"combat"}) | {"combat_units", "combat_group"}
    if set(COMMAND_TEMPLATES) != expected_forms:
        raise RuntimeError("Prompt templates must cover the actual action forms")
    rendered_fields = {}
    for form, template in COMMAND_TEMPLATES.items():
        entry = json.loads(re.sub(r"(?<!\")<(?:positive_integer|positive_number)>", "1", template))
        verb = entry["name"]
        arguments = dict(entry.get("arguments") or {})
        internal = {"action": verb, **arguments}
        rule = VERB_FIELD_RULES[verb]
        required = set(rule.required) | {"action"}
        if not required <= set(internal) <= rule.allowed:
            raise RuntimeError("Prompt templates must use the allowed action fields")
        if verb == "combat" and ("units" in arguments) == ("group" in arguments):
            raise RuntimeError("Prompt combat templates must select units or group")
        expected_verb = "combat" if form.startswith("combat_") else form
        if verb != expected_verb:
            raise RuntimeError("Prompt templates must match their action form")
        if form in {"combat_units", "combat_group"} and form.split("_", 1)[1] not in arguments:
            raise RuntimeError("Prompt combat templates must match their action form")
        rendered_fields.setdefault(verb, set()).update(internal)
    for verb, rule in VERB_FIELD_RULES.items():
        if rendered_fields.get(verb) != rule.allowed:
            raise RuntimeError("Prompt templates must cover the allowed action fields")

    return "\n".join([
        "Army:",
        ARMY_RULES.rstrip(),
        "",
        "Dispatch:",
        COMBAT_DISPATCH_RULES,
        "",
        "Retarget:",
        COMBAT_RETARGET_RULES,
        "",
        "Styles:",
        COMBAT_STYLE_RULES.rstrip(),
    ]) + "\n"


def render_observation_guide() -> str:
    return "\n".join([
        "Observation conventions:",
        "- The latest Current Observation is authoritative for changing game state and replaces older values; earlier observations are history.",
        "- unknown means that information is unavailable, while none means that the observed value is empty.",
        "- Visible enemies are current sightings; last-seen enemies are history under fog.",
        "- Waiting work, paid queues and living units are different quantities.",
        "- In Combat, Originally requested is the group's initial membership and Living members is its current surviving membership. Own Forces free, not phase alone, determines what can be newly dispatched.",
        "- Group phase reports progress only. Phase, nearest zone and nearby-enemy counts do not prove arrival or mission completion; use the current group list and recent events to determine whether a mission ended. combat_ended refers to the group mission, not the match result.",
        "- Names and IDs keep their exact platform spelling. Copy zone_id and group_id from Observation.",
    ]) + "\n"


def decision_json_schema(*, race: str = "terran") -> Dict[str, Any]:
    """Platform-owned JSON Schema for one decision array (draft-07 style)."""
    from sc2bench_env.interface.decision_rules import (
        decision_json_schema as _decision_json_schema,
    )

    return _decision_json_schema(race=race)


def catalog_as_dicts(*, race: str = "terran") -> List[Dict[str, Any]]:
    return [get_target(spec.name, race=race).to_dict() for spec in get_catalog(race=race).targets]


# Import-time consistency check.
validate_catalog()
