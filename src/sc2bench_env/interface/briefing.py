"""Compact turn briefing: relevant zones and currently available target names."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from sc2bench_env.interface.catalogs import get_catalog
from sc2bench_env.interface.races import require_supported_own_race


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
