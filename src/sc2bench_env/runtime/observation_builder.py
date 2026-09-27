"""Snapshot and demands to a structured Observation."""

from __future__ import annotations

from sc2bench_env.interface.observations import (
    EconomyView, GameView, MapControlView, Observation, ResourcesView, split_own_forces,
)

def build_observation(snapshot, task_manager, config, previous=None) -> Observation:
    assert config is not None
    under = dict(snapshot.info.get("under_construction") or {})
    in_prod = dict(snapshot.info.get("in_production_units") or {})
    in_research = snapshot.info.get("in_progress_research") or []
    if isinstance(in_research, dict):
        in_research = [key for key, value in in_research.items() if value]
    else:
        in_research = list(in_research)
    upgrades = list(snapshot.info.get("upgrades") or [])
    building = task_manager.building_summary(snapshot.buildings, under)
    training = task_manager.training_summary(in_prod)
    research = task_manager.research_summary(upgrades, in_research)
    scout_demand = next(
        (d for d in task_manager.active_demands() if d.action == "scout"),
        None,
    )
    race_view = build_race_view(
        race=config.race, snapshot=snapshot, scout_demand=scout_demand,
    )
    combat = combat_summary(snapshot, task_manager)
    limit = config.game_time_limit_seconds
    remaining = None
    if limit is not None:
        remaining = max(0.0, float(limit) - float(snapshot.game_time_seconds))
    # Count living permanent workers from the same entity inventory as
    # Own Forces, including loaded workers. Food-workers is not inventory.
    worker_count = sum(int(snapshot.units.get(name, 0)) for name in ("scv", "probe", "drone"))
    army_supply = int(
        snapshot.info.get(
            "supply_army",
            max(0, int(snapshot.supply_used) - worker_count),
        )
    )
    own_base_count = int(snapshot.info.get("base_count", sum(
        row.get("known_owner") == "self"
        for row in snapshot.info.get("zone_state") or []
    )))
    zones = list(snapshot.info.get("zones") or [])
    zone_state = list(snapshot.info.get("zone_state") or [])
    known_enemy = int(snapshot.info.get("known_enemy_base_count", 0))
    map_own_base_count = int(snapshot.info.get("own_base_count", own_base_count))
    unconfirmed = int(
        snapshot.info.get(
            "unconfirmed_expansion_count",
            max(0, len(zones) - map_own_base_count - known_enemy),
        )
    )
    observed_race = snapshot.info.get("identified_enemy_race")
    enemy_race = (observed_race if observed_race in {"terran", "protoss", "zerg"}
                  else "unknown" if config.enemy_race == "random" else config.enemy_race)
    resources = ResourcesView(
        minerals=snapshot.minerals,
        vespene=snapshot.vespene,
        supply_used=snapshot.supply_used,
        supply_cap=snapshot.supply_cap,
    )
    topology = dict(snapshot.info.get("map_topology") or {})
    relevant = relevant_zone_ids(
        {"zone_state": zone_state, "map_topology": topology, "combat": combat,
         "scouting": race_view.scouting},
        previous,
    )
    targets = available_targets(
        race=config.race, building=building,
        structures=list(snapshot.info.get("structures") or []),
        research=research,
        units=dict(snapshot.units),
    )
    return Observation(
        game_time_seconds=snapshot.game_time_seconds,
        race=config.race,
        enemy_race=enemy_race,
        resources=resources,
        game=GameView(
            game_time_seconds=float(snapshot.game_time_seconds),
            game_time_limit_seconds=limit,
            seconds_remaining=remaining,
            race=config.race,
            enemy_race=enemy_race,
        ),
        economy=EconomyView(
            minerals=snapshot.minerals,
            vespene=snapshot.vespene,
            supply_used=snapshot.supply_used,
            supply_cap=snapshot.supply_cap,
            supply_left=max(0, int(snapshot.supply_cap) - int(snapshot.supply_used)),
            worker_count=worker_count,
            mining_worker_capacity=snapshot.info.get("ideal_worker_count"),
            army_supply=army_supply,
            mineral_income_per_minute=snapshot.info.get("mineral_income_per_minute"),
            vespene_income_per_minute=snapshot.info.get("vespene_income_per_minute"),
        ),
        map_control=MapControlView(
            own_base_count=map_own_base_count,
            known_enemy_base_count=known_enemy,
            unconfirmed_expansion_count=unconfirmed,
            base_resources=list(snapshot.info.get("base_resources") or []),
        ),
        zone_state=zone_state,
        map_topology=topology,
        production_priority=task_manager.production_priority_summary(),
        production=snapshot.info.get("production"),
        own_forces=own_forces_view(snapshot, task_manager),
        abilities=race_view.abilities,
        units=dict(snapshot.units),
        buildings=dict(snapshot.buildings),
        building=building,
        training=training,
        research=research,
        combat=combat,
        scouting=race_view.scouting,
        upgrades=upgrades,
        structures=list(snapshot.info.get("structures") or []),
        base_count=own_base_count,
        zones=zones,
        **race_view.legacy_attributes,
        recent_events=list(task_manager.recent_events[-8:]),
        available_targets=targets,
        relevant_zone_ids=relevant,
        terminated=snapshot.terminated,
    )

def assigned_army_counts(snapshot, task_manager) -> dict[str, int]:
    bound: dict[str, int] = {}
    progress = dict(snapshot.info.get("combat_progress") or {})
    for demand in task_manager.active_demands():
        if demand.action != "combat":
            continue
        row = progress.get(demand.demand_id) or {}
        alive = row.get("alive")
        units = alive if isinstance(alive, dict) else (demand.units or {})
        for name, count in units.items():
            bound[str(name)] = bound.get(str(name), 0) + int(count)
    for name, count in dict(snapshot.info.get("unavailable_army") or {}).items():
        bound[str(name)] = max(bound.get(str(name), 0), int(count))
    return bound

def own_forces_view(snapshot, task_manager):
    return split_own_forces(
        snapshot.units,
        assigned=assigned_army_counts(snapshot, task_manager),
        bunker_garrison=snapshot.info.get("bunker_garrison"),
    )

def idle_army_counts(snapshot, task_manager) -> dict[str, int]:
    army = dict(split_own_forces(snapshot.units).army)
    bound = assigned_army_counts(snapshot, task_manager)
    return {
        name: max(0, int(army.get(name, 0)) - int(bound.get(name, 0)))
        for name in set(army) | set(bound)
    }

def combat_summary(snapshot, task_manager) -> dict[str, dict]:
    progress = dict(snapshot.info.get("combat_progress") or {})
    summary: dict[str, dict] = {}
    for demand in task_manager.active_demands():
        if demand.action != "combat":
            continue
        label = demand.group or "unassigned_group"
        row = progress.get(demand.demand_id) or {}
        alive = row.get("alive")
        if not isinstance(alive, dict):
            alive = dict(demand.units or {})
        current_style = row.get("style") or demand.style
        summary[label] = {
            "status": "active",
            "style": current_style,
            "target": demand.target,
            "requested": dict(demand.units or {}),
            "alive": {str(k): int(v) for k, v in alive.items()},
            "assigned": bool(row.get("assigned", True)),
            "nearest_zone": row.get("nearest_zone"),
        }
        # The backend changes a completed attack to defend so survivors hold
        # the cleared objective. Preserve the submitted order alongside the
        # current style so this does not look like an original defend order.
        if demand.style == "attack" and current_style == "defend":
            summary[label]["order_transition"] = {
                "from": "attack",
                "to": "defend",
                "reason": "target_confirmed_clear",
                "automatic": True,
            }
        if row.get("phase"):
            summary[label]["phase"] = "executing" if row["phase"] == "fight" else str(row["phase"])
        summary[label]["visible_enemy_nearby"] = row.get("visible_enemy_nearby")
        summary[label]["weapon_cooldown_active_count"] = row.get("weapon_cooldown_active_count")
        if "cloaked" in row:
            summary[label]["cloaked"] = dict(row["cloaked"])
        if row.get("forms"):
            summary[label]["forms"] = dict(row["forms"])
        if row.get("skill_evidence"):
            summary[label]["skill_evidence"] = dict(row["skill_evidence"])
        if "transport" in row:
            summary[label]["transport"] = dict(row["transport"])
        if row.get("end_reason"):
            summary[label]["end_reason"] = str(row["end_reason"])
    main_zone = next((z.get("zone_id") for z in snapshot.info.get("zone_state", [])
                      if z.get("zone_role") == "own_main"), None)
    group0_zone = snapshot.info.get("group0_zone_id") or main_zone
    summary["group_0"] = {
        "status": "active", "style": "defend", "target": group0_zone,
        "alive": {k: v for k, v in idle_army_counts(snapshot, task_manager).items() if v > 0},
        "assigned": False, "phase": "engaging" if snapshot.info.get("group0_engaged") else "guarding",
    }
    return summary

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from sc2bench_env.catalog.races import get_catalog
from sc2bench_env.interface.races import require_supported_own_race


@dataclass
class RaceObservationView:
    abilities: Dict[str, Any] = field(default_factory=dict)
    scouting: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    legacy_attributes: Dict[str, Any] = field(default_factory=dict)


def _known_int(value: Any) -> Optional[int]:
    return None if value is None else int(value)


def _scout_row(snapshot, scout_demand, worker_key: str) -> Dict[str, Dict[str, Any]]:
    scouting: Dict[str, Dict[str, Any]] = {}
    if scout_demand is None:
        return scouting
    info = snapshot.info
    row: Dict[str, Any] = {
        'status': scout_demand.state.value,
        'route': list(scout_demand.route or ()) if scout_demand.route != 'all' else None,
    }
    progress = dict(info.get('scout_progress') or {})
    if scout_demand.route == 'all':
        row['mode'] = 'all_expansions'
        actual_route = progress.get('route')
        row['route'] = list(actual_route) if isinstance(actual_route, (list, tuple)) else None
    moving_to = progress.get('moving_to')
    if moving_to:
        row['moving_to'] = str(moving_to)
    if 'waypoint_index' in progress:
        row['waypoint_index'] = _known_int(progress['waypoint_index'])
    if 'assigned' in progress:
        assigned = progress['assigned']
        row['assigned'] = None if assigned is None else bool(assigned)
    scouting[worker_key] = row
    return scouting


def build_terran_view(*, snapshot, scout_demand=None) -> RaceObservationView:
    info = snapshot.info
    orbital_count = _known_int(info.get('orbital_count', snapshot.buildings.get('orbital_command', 0)))
    scan_ready = _known_int(info.get('scan_ready', 0))
    mule_ready = _known_int(info.get('mule_ready', info.get('scan_ready', 0)))
    energies = info.get('orbital_energies', [])
    return RaceObservationView(
        abilities={
            'scan_ready': scan_ready,
            'mule_ready': mule_ready,
            'orbital_energies': None if energies is None else list(energies),
        },
        scouting=_scout_row(snapshot, scout_demand, 'scv'),
        legacy_attributes={
            'orbital_count': orbital_count,
            'scan_ready': scan_ready,
            'mule_ready': mule_ready,
        },
    )


def build_protoss_view(*, snapshot, scout_demand=None) -> RaceObservationView:
    info = snapshot.info
    energies = info.get('nexus_energies', [])
    return RaceObservationView(
        abilities={
            'chrono_ready': _known_int(info.get('chrono_ready', 0)),
            'nexus_energies': None if energies is None else list(energies),
        },
        scouting=_scout_row(snapshot, scout_demand, 'probe'),
    )


def build_zerg_view(*, snapshot, scout_demand=None) -> RaceObservationView:
    info = snapshot.info
    energies = info.get('queen_energies', [])
    return RaceObservationView(
        abilities={
            'inject_ready': _known_int(info.get('inject_ready', 0)),
            'queen_energies': None if energies is None else list(energies),
        },
        scouting=_scout_row(snapshot, scout_demand, 'drone'),
    )


def build_race_view(*, race: str, snapshot, scout_demand=None) -> RaceObservationView:
    require_supported_own_race(race)
    if race == 'terran':
        return build_terran_view(snapshot=snapshot, scout_demand=scout_demand)
    if race == 'protoss':
        return build_protoss_view(snapshot=snapshot, scout_demand=scout_demand)
    if race == 'zerg':
        return build_zerg_view(snapshot=snapshot, scout_demand=scout_demand)
    raise ValueError(f'No Observation view implemented for own race {race!r}')


def _positive_counts(contents: Any) -> bool:
    if not isinstance(contents, Mapping):
        return False
    for key in ("units", "buildings"):
        items = contents.get(key) or {}
        if isinstance(items, Mapping) and any(
            isinstance(count, (int, float)) and count > 0 for count in items.values()
        ):
            return True
    return False


def _ready_count(building: Mapping[str, Any], name: str) -> int:
    row = building.get(name) or {}
    if not isinstance(row, Mapping):
        return 0
    completed = row.get("completed")
    return int(completed) if type(completed) is int and completed > 0 else 0


def _observation_units(observation: Mapping[str, Any]) -> Dict[str, int]:
    raw = observation.get("units")
    if isinstance(raw, Mapping) and raw:
        return {str(name): int(count) for name, count in raw.items() if type(count) is int and count > 0}
    forces = observation.get("own_forces") or {}
    counts: Dict[str, int] = {}
    if not isinstance(forces, Mapping):
        return counts
    for key in ("workers", "army"):
        row = forces.get(key) or {}
        if not isinstance(row, Mapping):
            continue
        for name, count in row.items():
            if type(count) is int and count > 0:
                counts[str(name)] = counts.get(str(name), 0) + count
    return counts


def _living_count(units: Mapping[str, Any], name: str) -> int:
    count = units.get(name, 0)
    return int(count) if type(count) is int and count > 0 else 0


def available_targets(
    *,
    race: str,
    building: Optional[Mapping[str, Any]] = None,
    structures: Optional[Sequence[Mapping[str, Any]]] = None,
    research: Optional[Mapping[str, Any]] = None,
    units: Optional[Mapping[str, Any]] = None,
) -> Dict[str, List[str]]:
    """Tech and producer availability. Finished or running research is omitted.

    Resources, supply and free slots do not hide names. A completed research
    still satisfies the next level. A morph unit is listed only while its
    source unit is alive.
    """
    require_supported_own_race(race)
    building = building or {}
    units = units or {}
    structures = list(structures or ())
    catalog = get_catalog(race=race)
    structure_types = {str(row.get("type")) for row in structures if isinstance(row, Mapping)}
    finished, running = set(), set()
    if isinstance(research, Mapping):
        for name, status in research.items():
            text = str(status)
            if text == "completed":
                finished.add(str(name))
            elif text == "in_progress":
                running.add(str(name))

    def ready(name: str) -> bool:
        return (
            _ready_count(building, name) > 0
            or name in structure_types
            or name in finished
            or _living_count(units, name) > 0
        )

    def prereqs_met(spec) -> bool:
        return all(ready(req) for req in spec.prerequisites)

    grouped: Dict[str, List[str]] = {"build": [], "train": [], "research": [], "upgrade": []}
    for spec in catalog.targets:
        if spec.action not in grouped:
            continue
        if spec.action == "build" and prereqs_met(spec):
            grouped["build"].append(spec.name)
        elif spec.action == "train" and ready(spec.produced_at) and prereqs_met(spec):
            grouped["train"].append(spec.name)
        elif (
            spec.action == "research"
            and spec.name not in finished
            and spec.name not in running
            and ready(spec.produced_at)
            and prereqs_met(spec)
        ):
            grouped["research"].append(spec.name)
        elif spec.action == "upgrade" and ready(spec.morph_from) and prereqs_met(spec):
            grouped["upgrade"].append(spec.name)
    return grouped


def relevant_zone_ids(
    observation: Mapping[str, Any],
    previous: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """Zones needed for the current decision; the complete map remains queryable.

    Do not treat routine count changes or corridor adjacency as relevance. Mining,
    production and moving armies change those values almost every turn and used to
    expand the briefing back to nearly the whole map.
    """
    del previous  # Retained for compatibility with existing callers.
    zones = list(observation.get("zone_state") or [])
    selected: set[str] = set()
    for row in zones:
        zone_id = row.get("zone_id")
        if not isinstance(zone_id, str):
            continue
        if row.get("known_owner") == "self":
            selected.add(zone_id)
        if row.get("known_owner") == "enemy":
            selected.add(zone_id)
        if row.get("visible_enemy_weapon_in_range"):
            selected.add(zone_id)
        if _positive_counts(row.get("visible_enemy_contents")) or _positive_counts(
            row.get("last_seen_enemy_contents")
        ):
            selected.add(zone_id)

    for row in (observation.get("combat") or {}).values():
        if not isinstance(row, Mapping):
            continue
        for key in ("target", "nearest_zone"):
            value = row.get(key)
            if isinstance(value, str) and value.startswith("zone_"):
                selected.add(value)

    for row in (observation.get("scouting") or {}).values():
        if not isinstance(row, Mapping):
            continue
        moving = row.get("moving_to")
        if isinstance(moving, str) and moving.startswith("zone_"):
            selected.add(moving)
        route = row.get("route")
        if isinstance(route, list):
            for item in route[:1]:
                if isinstance(item, str) and item.startswith("zone_"):
                    selected.add(item)

    return [row["zone_id"] for row in zones if row.get("zone_id") in selected]


def compact_map_control(
    map_control: Mapping[str, Any], relevant: Iterable[str]
) -> Dict[str, Any]:
    allowed = set(relevant)
    payload = dict(map_control)
    resources = []
    for row in map_control.get("base_resources") or []:
        if not isinstance(row, Mapping) or row.get("zone_id") not in allowed:
            continue
        if row.get("resource_visibility") != "visible":
            continue
        # Initial totals, visibility and total geyser slots are stable or
        # derivable. Keep only the current values needed for a turn decision.
        resources.append({
            key: row.get(key)
            for key in (
                "zone_id", "minerals_remaining", "vespene_remaining",
                "owned_gas_structure_count", "available_geyser_slots",
            )
        })
    payload["base_resources"] = resources
    return payload


def compact_production(rows: Optional[Sequence[Mapping[str, Any]]]) -> Optional[List[dict[str, Any]]]:
    if rows is None:
        return None
    standard = {
        "facility", "ready_grounded", "techlab_hosts", "reactor_hosts",
        "capacity", "occupied_slots", "free_slots", "queue_capacity",
        "queued_orders", "free_queue_positions", "free_techlab_slots",
    }

    def meaningful(row: Mapping[str, Any]) -> bool:
        if any(
            type(item) in {int, float} and item > 0
            for key, item in row.items() if key != "facility"
        ):
            return True
        return any(
            key not in standard and item not in (None, "", False, 0, [], {})
            for key, item in row.items()
        )

    return [
        dict(row) for row in rows
        if row.get("facility") and meaningful(row)
    ]


def compact_observation(
    observation: Mapping[str, Any],
    previous: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Text-facing copy: full structured Observation is unchanged for tools."""
    payload = dict(observation)
    relevant = relevant_zone_ids(observation, previous)
    payload["relevant_zone_ids"] = relevant
    zones = observation.get("zone_state")
    if zones:
        payload["zone_state"] = [
            row for row in zones
            if isinstance(row, Mapping) and row.get("zone_id") in set(relevant)
        ]
        if isinstance(observation.get("map_control"), Mapping):
            payload["map_control"] = compact_map_control(observation["map_control"], relevant)

    # A match is one Agent session. Race and the fixed time limit are part of
    # the opening briefing; later turns retain only changing clock values.
    if previous is not None and isinstance(observation.get("game"), Mapping):
        game = observation["game"]
        payload["game"] = {
            key: game.get(key)
            for key in ("game_time_seconds", "seconds_remaining")
            if key in game
        }

    # Economy already owns the live worker total. Do not repeat the same count
    # in Own Forces, while preserving it for partial observations without an
    # economy section.
    if isinstance(observation.get("economy"), Mapping) and isinstance(
        observation.get("own_forces"), Mapping
    ):
        forces = dict(observation["own_forces"])
        workers = forces.get("workers")
        worker_count = observation["economy"].get("worker_count")
        if (
            isinstance(workers, Mapping)
            and type(worker_count) is int
            and all(type(count) is int for count in workers.values())
            and sum(workers.values()) == worker_count
        ):
            forces.pop("workers", None)
        payload["own_forces"] = forces

    # Own Forces is the authoritative source for home-pool availability.
    # Combat still reports group_0's order and phase.
    if isinstance(observation.get("own_forces"), Mapping) and isinstance(
        observation.get("combat"), Mapping
    ):
        combat = {
            str(name): dict(row) if isinstance(row, Mapping) else row
            for name, row in observation["combat"].items()
        }
        home = combat.get("group_0")
        if isinstance(home, dict):
            free = observation["own_forces"].get("free")
            if isinstance(free, Mapping) and home.get("alive") == free:
                home.pop("alive", None)
                home.pop("assigned", None)
        payload["combat"] = combat
    if "production" in observation:
        payload["production"] = compact_production(observation.get("production"))
    payload.pop("map_topology", None)
    race = "terran"
    game = observation.get("game")
    if isinstance(game, Mapping) and game.get("race"):
        race = str(game["race"])
    if observation.get("available_targets"):
        payload["available_targets"] = observation["available_targets"]
    else:
        try:
            payload["available_targets"] = available_targets(
                race=race,
                building=observation.get("building"),
                structures=observation.get("structures"),
                research=observation.get("research") if isinstance(observation.get("research"), Mapping) else None,
                units=_observation_units(observation),
            )
        except ValueError:
            payload["available_targets"] = {}
    return payload
