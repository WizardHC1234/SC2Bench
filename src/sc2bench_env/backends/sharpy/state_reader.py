"""Read Sharpy/SC2 state into platform-facing counts."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sc2bench_env.backends.base import BackendSnapshot
from sc2bench_env.backends.sharpy.races.base import RaceAdapter
from sc2bench_env.backends.sharpy.structures import StructureRegistry
from sc2bench_env.backends.sharpy.zone_contents import (
    is_currently_visible_enemy,
    summarize_entities,
)
from sc2bench_env.backends.sharpy.zones import ZoneRegistry
from sc2bench_env.backends.sharpy.weapon_facts import has_active_weapon

def _bump(counter: Dict[str, int], key: str, amount: int = 1) -> None:
    if not key:
        return
    counter[key] = counter.get(key, 0) + amount


def _townhall_count(buildings: Dict[str, int], targets) -> Optional[int]:
    return None if targets is None else sum(int(buildings.get(key, 0)) for key in targets)


def _structure_type_name(structure) -> str:
    return str(getattr(getattr(structure, "type_id", None), "name", "")).upper()


def _count_new_passengers(
    carriers,
    *,
    adapter,
    units: Dict[str, int],
    unavailable_army: Dict[str, int],
    ready_unit_tags: Dict[str, List[int]],
    counted_tags: set,
    garrison: Dict[str, int] | None = None,
) -> None:
    """Add passengers that are not already present as on-map units."""
    garrison_tags: set[int] = set()
    for carrier in carriers:
        for passenger in getattr(carrier, "passengers", []) or []:
            tag = getattr(passenger, "tag", None)
            if tag is None:
                continue
            tag = int(tag)
            type_name = getattr(getattr(passenger, "type_id", None), "name", "")
            name = adapter.normalize_unit_name(type_name)
            if name is None:
                continue
            if garrison is not None and _structure_type_name(carrier) == "BUNKER" and tag not in garrison_tags:
                garrison_tags.add(tag)
                _bump(garrison, name)
            if tag in counted_tags:
                continue
            counted_tags.add(tag)
            _bump(units, name)
            ready_unit_tags.setdefault(name, []).append(tag)
            if name not in {"scv", "probe", "drone", "mule"}:
                _bump(unavailable_army, name)


def _visible_weapon_enemies(ai) -> List[Any]:
    """Exclude fog memory and inactive weapons from the platform threat view."""
    enemies = getattr(ai, "all_enemy_units", None)
    if enemies is None:
        enemies = list(getattr(ai, "enemy_units", []) or []) + list(
            getattr(ai, "enemy_structures", []) or []
        )
    return [enemy for enemy in enemies
            if getattr(enemy, "is_visible", False)
            and not getattr(enemy, "is_memory", False)
            and not getattr(enemy, "is_snapshot", False)
            and not getattr(enemy, "is_hallucination", False)
            and has_active_weapon(enemy)]


def _zone_has_visible_weapon_threat(zone, enemies, unit_values) -> bool:
    """Current weapon-range threat, not relative power or proof of damage.

    Targets include workers and buildings. Check all visible enemies, not only
    the zone's enemy list, so weapons can threaten across a zone boundary.
    Spell-only threats and hidden enemies are not inferred by this predicate.
    """
    for own in getattr(zone, "our_units", []) or []:
        for enemy in enemies:
            if unit_values is not None:
                attack_range = unit_values.real_range(enemy, own)
                if attack_range > 0 and enemy.distance_to(own) <= attack_range:
                    return True
            elif enemy.target_in_range(own):
                return True
    return False


def _known_zone_owner(zone) -> str:
    """Confirmed townhall ownership; never Sharpy's strategic presumption."""
    if getattr(zone, "our_townhall", None) is not None:
        return "self"
    if getattr(zone, "enemy_townhall", None) is not None:
        return "enemy"
    return "unconfirmed"


def _entity_key(entity: Any) -> Tuple[str, int]:
    tag = getattr(entity, "tag", None)
    return ("tag", int(tag)) if tag is not None else ("object", id(entity))


def _distance_to_center(entity: Any, center: Any) -> float:
    try:
        return float(entity.distance_to(center))
    except Exception:
        position = getattr(entity, "position", entity)
        try:
            return float(center.distance_to(position))
        except Exception:
            ex = float(getattr(position, "x", 0.0))
            ey = float(getattr(position, "y", 0.0))
            return ((ex - float(center.x)) ** 2 + (ey - float(center.y)) ** 2) ** 0.5


def _assign_entities_to_zones(
    zones: List[Any],
    zone_ids: List[str],
    entities: List[Any],
    *,
    membership_attribute: str,
) -> Dict[str, List[Any]]:
    """Use Sharpy membership first and give every remaining entity a Zone.

    Nearest-center fallback prevents mobile units between expansion radii from
    silently disappearing from the platform's map abstraction.
    """
    assigned: Dict[str, List[Any]] = {zone_id: [] for zone_id in zone_ids}
    allowed = {_entity_key(entity): entity for entity in entities}
    seen = set()
    for zone, zone_id in zip(zones, zone_ids):
        for entity in list(getattr(zone, membership_attribute, None) or []):
            key = _entity_key(entity)
            if key not in allowed or key in seen:
                continue
            assigned[zone_id].append(allowed[key])
            seen.add(key)
    for key, entity in allowed.items():
        if key in seen or not zones:
            continue
        index = min(
            range(len(zones)),
            key=lambda value: _distance_to_center(
                entity, getattr(zones[value], "center_location")
            ),
        )
        assigned[zone_ids[index]].append(entity)
        seen.add(key)
    return assigned


def _place_loaded_passengers_with_transport(
    assigned: Dict[str, List[Any]],
) -> None:
    """Move every loaded passenger to its transport's Zone and count it once."""
    passenger_targets: Dict[Tuple[str, int], Tuple[str, Any]] = {}
    for zone_id, entities in assigned.items():
        for transport in list(entities):
            for passenger in list(getattr(transport, "passengers", None) or []):
                passenger_targets[_entity_key(passenger)] = (zone_id, passenger)
    if not passenger_targets:
        return
    cargo_keys = set(passenger_targets)
    for zone_id, entities in assigned.items():
        assigned[zone_id] = [entity for entity in entities
                             if _entity_key(entity) not in cargo_keys]
    for _key, (zone_id, passenger) in passenger_targets.items():
        assigned[zone_id].append(passenger)


def read_snapshot(
    ai,
    adapter: RaceAdapter,
    *,
    zone_registry: Optional[ZoneRegistry] = None,
    structure_registry: Optional[StructureRegistry] = None,
) -> Tuple[BackendSnapshot, Dict[str, int], Dict[str, int], Dict[str, int]]:
    """Return snapshot plus in-progress counts.

    Returns:
        snapshot, in_production_units, in_progress_buildings, in_progress_research
    """
    units: Dict[str, int] = {}
    buildings: Dict[str, int] = {}
    in_production_units: Dict[str, int] = {}
    in_progress_buildings: Dict[str, int] = {}
    in_progress_research: Dict[str, int] = {}
    workers_en_route: Dict[str, int] = {}
    unknown_structures: List[str] = []
    unknown_units: List[str] = []
    townhall_rows: List[Tuple[int, str]] = []
    ready_unit_tags: Dict[str, List[int]] = {}
    building_entity_tags: Dict[str, List[int]] = {}
    unavailable_army: Dict[str, int] = {}
    townhall_targets = getattr(adapter, "townhall_targets", None)

    for unit in ai.structures:
        name = adapter.normalize_unit_name(unit.type_id.name)
        if name is None:
            unknown_structures.append(str(unit.type_id.name))
            continue
        building_entity_tags.setdefault(name, []).append(int(unit.tag))
        if unit.build_progress < 1:
            _bump(in_progress_buildings, name)
            if townhall_targets is not None and name in townhall_targets:
                _bump(in_progress_buildings, "base")
        else:
            _bump(buildings, name)
            if townhall_targets is not None and name in townhall_targets:
                townhall_rows.append((int(unit.tag), name))

    for unit in ai.units:
        if unit.is_structure:
            continue
        name = adapter.normalize_unit_name(unit.type_id.name)
        if name is None:
            unknown_units.append(str(unit.type_id.name))
            continue
        if unit.build_progress < 1:
            continue
        _bump(units, name)
        ready_unit_tags.setdefault(name, []).append(int(unit.tag))
        if name not in {"scv", "probe", "drone", "mule"}:
            from sc2bench_env.backends.sharpy.combat_styles import available_for_mission

            pool_tags = getattr(ai, "bench_group0_tags", None)
            if ((pool_tags is not None and unit.tag not in pool_tags)
                    or not available_for_mission(unit, getattr(ai, "roles", None), getattr(ai, "bench_combat_tags", set()))):
                _bump(unavailable_army, name)

        # Detect workers that already accepted a build command but the structure
        # entity may not exist yet.
        resolve_build = getattr(adapter, "worker_build_target", None)
        if callable(resolve_build):
            for order in getattr(unit, "orders", []) or []:
                target = resolve_build(name, order)
                if target:
                    _bump(workers_en_route, target)

    # Loaded passengers and bunker Marines disappear from ai.units but remain living.
    visible_tags = {int(unit.tag) for unit in ai.units}
    bunker_garrison: Dict[str, int] = {}
    bunkers = [
        structure for structure in getattr(ai, "structures", []) or []
        if _structure_type_name(structure) == "BUNKER"
        and float(getattr(structure, "build_progress", 1) or 0) >= 1
    ]
    _count_new_passengers(
        list(ai.units) + bunkers,
        adapter=adapter,
        units=units,
        unavailable_army=unavailable_army,
        ready_unit_tags=ready_unit_tags,
        counted_tags=set(visible_tags),
        garrison=bunker_garrison,
    )

    for structure in ai.structures.ready:
        for order in structure.orders:
            resolve_order = getattr(adapter, "production_order_target", None)
            decoded = (resolve_order(order, getattr(ai, "_game_data", None))
                       if callable(resolve_order) else None)
            if decoded is not None:
                action, target = decoded
                counters = {"train": in_production_units, "build": in_progress_buildings,
                            "research": in_progress_research}
                _bump(counters[action], target)

    upgrades: List[str] = []
    normalize_upgrade = getattr(adapter, "normalize_upgrade_name", None)
    for upgrade in getattr(ai, "state", None).upgrades if getattr(ai, "state", None) else []:
        name = None
        raw = getattr(upgrade, "name", str(upgrade))
        if callable(normalize_upgrade):
            name = normalize_upgrade(raw)
        if name:
            upgrades.append(name)

    townhall_units = getattr(ai, "townhalls", None)
    base_count = len(townhall_units) if townhall_units is not None else _townhall_count(buildings, townhall_targets)

    zone_manager = getattr(ai, "zone_manager", None)
    expansion_zones = list(getattr(zone_manager, "expansion_zones", None) or [])
    enemies = _visible_weapon_enemies(ai)
    unit_values = getattr(getattr(ai, "knowledge", None), "unit_values", None)
    if unit_values is None:
        unit_values = getattr(ai, "unit_values", None)
    centers = []
    observed_zones: List[Any] = []
    registry = zone_registry or ZoneRegistry()
    registry.sync_roles(ai, zone_manager, expansion_zones)
    zone_rows: List[Dict[str, Any]] = []
    base_resources: List[Dict[str, Any]] = []
    for zone in expansion_zones:
        center = getattr(zone, "center_location", None)
        if center is None:
            continue
        observed_zones.append(zone)
        centers.append((float(center.x), float(center.y)))
        known_owner = _known_zone_owner(zone)
        is_visible = getattr(ai, "is_visible", lambda position: False)
        row: Dict[str, Any] = {
            "zone_id": "",
            "zone_role": registry.role_for_center(float(center.x), float(center.y)),
            "known_owner": known_owner,
            "vision_state": "visible" if is_visible(center) else "fogged",
            "visible_enemy_weapon_in_range": _zone_has_visible_weapon_threat(
                zone, enemies, unit_values
            ),
        }
        zone_rows.append(row)
        base_resources.append(registry.resources.read_zone(ai, zone))
    zones = registry.sync_from_centers(centers)
    group0_zone_id = None
    group0_home = getattr(ai, "bench_group0_home_point", None)
    if group0_home is not None and observed_zones:
        index = min(
            range(len(observed_zones)),
            key=lambda i: observed_zones[i].center_location.distance_to(group0_home),
        )
        group0_zone_id = zones[index]

    own_entities = list(getattr(ai, "units", None) or []) + list(
        getattr(ai, "structures", None) or []
    )
    own_by_zone = _assign_entities_to_zones(
        observed_zones, zones, own_entities, membership_attribute="our_units"
    )
    _place_loaded_passengers_with_transport(own_by_zone)
    all_enemy_entities = getattr(ai, "all_enemy_units", None)
    if all_enemy_entities is None:
        all_enemy_entities = list(getattr(ai, "enemy_units", None) or []) + list(
            getattr(ai, "enemy_structures", None) or []
        )
    current_enemy_entities = [
        entity for entity in all_enemy_entities if is_currently_visible_enemy(entity)
    ]
    enemy_by_zone = _assign_entities_to_zones(
        observed_zones,
        zones,
        current_enemy_entities,
        membership_attribute="known_enemy_units",
    )
    enemy_views = registry.enemies.observe(
        ai=ai,
        adapter=adapter,
        visible_by_zone=enemy_by_zone,
        now=float(getattr(ai, "time", 0.0) or 0.0),
    )
    for zone_id, row in zip(zones, zone_rows):
        row["zone_id"] = zone_id
        row["own_contents"] = summarize_entities(own_by_zone[zone_id], adapter)
        row.update(enemy_views[zone_id])
    for zone_id, row in zip(zones, base_resources):
        row["zone_id"] = zone_id
    from sc2bench_env.backends.sharpy.zone_topology import read_topology

    map_topology = read_topology(registry, observed_zones)
    zones_under_attack = [
        row["zone_id"] for row in zone_rows if row["visible_enemy_weapon_in_range"]
    ]
    own_zone_count = sum(1 for row in zone_rows if row["known_owner"] == "self")
    enemy_zone_count = sum(1 for row in zone_rows if row["known_owner"] == "enemy")
    unconfirmed_zone_count = sum(1 for row in zone_rows if row["known_owner"] == "unconfirmed")
    supply_workers = int(getattr(
        ai, "supply_workers", sum(units.get(name, 0) for name in ("scv", "probe", "drone"))
    ) or 0)
    supply_army = int(getattr(ai, "supply_army", 0) or 0)
    townhalls = getattr(townhall_units, "ready", None)
    gas_buildings = getattr(getattr(ai, "gas_buildings", None), "ready", None)
    ideal_worker_count = None
    if townhalls is not None and gas_buildings is not None:
        ready_harvester_sites = list(townhalls) + list(gas_buildings)
        if all(getattr(structure, "ideal_harvesters", None) is not None
               for structure in ready_harvester_sites):
            ideal_worker_count = sum(
                max(0, int(structure.ideal_harvesters))
                for structure in ready_harvester_sites
            )
    # Sharpy's default IncomeCalculator estimates collection from assigned
    # harvesters. The SC2 score exposes the observed collection rate directly.
    score = getattr(getattr(ai, "state", None), "score", None)
    mineral_rate = getattr(score, "collection_rate_minerals", None)
    vespene_rate = getattr(score, "collection_rate_vespene", None)
    mineral_per_min = round(float(mineral_rate), 1) if mineral_rate is not None else None
    vespene_per_min = round(float(vespene_rate), 1) if vespene_rate is not None else None

    struct_registry = structure_registry or StructureRegistry()
    structures = struct_registry.sync_townhalls(townhall_rows)

    read_abilities = getattr(adapter, "read_ability_facts", None)
    ability_facts = read_abilities(ai, buildings) if callable(read_abilities) else {}
    under_construction = {
        key: value for key, value in in_progress_buildings.items() if key not in {"base"}
    }

    snapshot = BackendSnapshot(
        game_time_seconds=float(getattr(ai, "time", 0.0) or 0.0),
        minerals=int(getattr(ai, "minerals", 0) or 0),
        vespene=int(getattr(ai, "vespene", 0) or 0),
        supply_used=int(getattr(ai, "supply_used", 0) or 0),
        supply_cap=int(getattr(ai, "supply_cap", 0) or 0),
        units=units,
        buildings=buildings,
        info={
            # Audit metadata only; Environment does not include it in model Observation.
            "game_version": getattr(getattr(getattr(ai, "knowledge", None),
                                            "version_manager", None), "full_version", None) or None,
            "identified_enemy_race": _identified_enemy_race(ai),
            "production": _production_capacity(ai, adapter),
            "backend": "sharpy",
            "ready_unit_tags": ready_unit_tags,
            "building_entity_tags": building_entity_tags,
            "unavailable_army": unavailable_army,
            "bunker_garrison": bunker_garrison,
            "group0_engaged": bool(getattr(ai, "bench_group0_engaged", False)),
            "group0_zone_id": group0_zone_id,
            "base_count": base_count,
            "upgrades": sorted(set(upgrades)),
            "zones": zones,
            "zones_under_attack": zones_under_attack,
            "zone_state": zone_rows,
            "base_resources": base_resources,
            "structures": structures,
            **ability_facts,
            "under_construction": under_construction,
            "workers_en_route": dict(workers_en_route),
            "in_production_units": dict(in_production_units),
            "in_progress_buildings": dict(in_progress_buildings),
            "in_progress_research": dict(in_progress_research),
            "unknown_structures": sorted(set(unknown_structures)),
            "unknown_units": sorted(set(unknown_units)),
            "supply_workers": supply_workers,
            "supply_army": supply_army,
            "ideal_worker_count": ideal_worker_count,
            "mineral_income_per_minute": mineral_per_min,
            "vespene_income_per_minute": vespene_per_min,
            "known_enemy_base_count": enemy_zone_count,
            "own_base_count": own_zone_count,
            "unconfirmed_expansion_count": unconfirmed_zone_count,
            "map_topology": map_topology,
        },
    )
    return snapshot, in_production_units, in_progress_buildings, in_progress_research


def _identified_enemy_race(ai):
    # python-sc2 updates enemy_race after seeing a Random opponent's units.
    name = str(getattr(getattr(ai, "enemy_race", None), "name", "")).lower()
    return name if name in {"terran", "protoss", "zerg"} else None


def _production_capacity(ai, adapter):
    """Delegate race-specific queue facts; never fall back to Terran rules."""
    reader = getattr(adapter, "read_production_capacity", None)
    return reader(ai) if callable(reader) else None
