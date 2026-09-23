"""Defensive building positions. Agent build() stays coordinate-free.

Legal grid points are scored here. When no defensive spot is usable, the
caller falls back to the normal Terran grid so the build does not stall.
"""

from __future__ import annotations

from math import hypot
from typing import Any, Optional, Sequence

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts import GridBuilding

TURRET_RANGE = 7.0
TURRET_OVERLAP = 6.0
RAMP_CLEARANCE = 3.5
DEFENSE_SEPARATION = 2.5
SENSOR_MAX_FROM_BASE = 22.0
DEFENSE_KINDS = frozenset({
    "bunker", "missile_turret", "sensor_tower", "photon_cannon", "shield_battery",
    "spine_crawler", "spore_crawler",
})
DEFENSE_TYPES = {
    "bunker": UnitTypeId.BUNKER,
    "missile_turret": UnitTypeId.MISSILETURRET,
    "sensor_tower": UnitTypeId.SENSORTOWER,
    "photon_cannon": UnitTypeId.PHOTONCANNON,
    "shield_battery": UnitTypeId.SHIELDBATTERY,
    "spine_crawler": UnitTypeId.SPINECRAWLER,
    "spore_crawler": UnitTypeId.SPORECRAWLER,
}


def _xy(point) -> tuple[float, float]:
    if isinstance(point, tuple):
        return (float(point[0]), float(point[1]))
    return (float(point.x), float(point.y))


def _dist(left, right) -> float:
    ax, ay = _xy(left)
    bx, by = _xy(right)
    return hypot(ax - bx, ay - by)


def _nearest(point, others: Sequence[Any]) -> float:
    if not others:
        return 1e9
    return min(_dist(point, other) for other in others)


def choose_defense_position(
    kind: str,
    candidates: Sequence[Any],
    *,
    ramp_tops: Sequence[Any] = (),
    threatened_ramps: Sequence[Any] = (),
    ramp_bottoms: Sequence[Any] = (),
    minerals: Sequence[Any] = (),
    production: Sequence[Any] = (),
    existing: Sequence[Any] = (),
    townhalls: Sequence[Any] = (),
    map_center: Any = None,
    natural: Any = None,
) -> Any:
    """Return one candidate, or None when the normal grid should be used."""
    usable = [
        point for point in candidates
        if _nearest(point, existing) >= DEFENSE_SEPARATION
        and (kind != "bunker" or _nearest(point, ramp_bottoms) >= RAMP_CLEARANCE)
    ]
    if not usable:
        return None
    scored = {
        "photon_cannon": "missile_turret", "shield_battery": "missile_turret",
        "spine_crawler": "missile_turret", "spore_crawler": "missile_turret",
    }.get(kind, kind)
    if scored == "bunker":
        return _choose_bunker(usable, ramp_tops, threatened_ramps, production)
    if scored == "missile_turret":
        return _choose_turret(usable, minerals, production, existing, townhalls, natural)
    if kind == "sensor_tower":
        return _choose_sensor(usable, townhalls, map_center)
    return None


def _choose_bunker(candidates, ramp_tops, threatened_ramps, production):
    if threatened_ramps:
        point = min(candidates, key=lambda item: _nearest(item, threatened_ramps))
        if _nearest(point, threatened_ramps) <= 10:
            return point
    behind = [
        point for point in candidates
        if 2 <= _nearest(point, ramp_tops) <= 8
    ]
    if behind:
        return min(behind, key=lambda item: _nearest(item, ramp_tops))
    near_production = [
        point for point in candidates
        if 3 <= _nearest(point, production) <= 8
    ]
    if near_production:
        return min(near_production, key=lambda item: _nearest(item, production))
    return None


def _covers(point, targets, existing) -> int:
    covered = 0
    for target in targets:
        if _dist(point, target) > TURRET_RANGE:
            continue
        if any(_dist(target, tower) <= TURRET_RANGE for tower in existing):
            continue
        covered += 1
    return covered


def _choose_turret(candidates, minerals, production, existing, townhalls, natural):
    targets = list(minerals) + list(production)
    air = None
    if natural is not None and townhalls:
        home = _xy(townhalls[0])
        other = _xy(natural)
        air = ((home[0] + other[0]) / 2, (home[1] + other[1]) / 2)
    best = None
    best_key = None
    for point in candidates:
        if any(_dist(point, tower) < TURRET_OVERLAP for tower in existing):
            continue
        cover = _covers(point, targets, existing)
        air_hit = 1 if air is not None and _dist(point, air) <= 8 else 0
        if cover <= 0 and not air_hit:
            continue
        key = (cover, air_hit, -_nearest(point, minerals or production or townhalls))
        if best_key is None or key > best_key:
            best, best_key = point, key
    if best is not None:
        return best
    if minerals:
        point = min(candidates, key=lambda item: _nearest(item, minerals))
        if _nearest(point, minerals) <= 12:
            return point
    return None


def _choose_sensor(candidates, townhalls, map_center):
    if not townhalls or map_center is None:
        return None
    center = _xy(map_center)
    best = None
    best_distance = -1.0
    for point in candidates:
        home = min(townhalls, key=lambda hall: _dist(point, hall))
        distance = _dist(point, home)
        if distance > SENSOR_MAX_FROM_BASE:
            continue
        origin = _xy(home)
        outward = (center[0] - origin[0], center[1] - origin[1])
        delta = (_xy(point)[0] - origin[0], _xy(point)[1] - origin[1])
        if outward[0] * delta[0] + outward[1] * delta[1] <= 0:
            continue
        if distance > best_distance:
            best, best_distance = point, distance
    return best


class DefensiveGridBuilding(GridBuilding):
    """Grid build that prefers a defensive spot, then the normal Terran grid."""

    def __init__(self, unit_type: UnitTypeId, to_count: int, kind: str):
        super().__init__(unit_type, to_count, allow_wall=False)
        self.kind = kind

    def position_terran(self, count) -> Optional[Any]:
        chosen = self._defensive_position()
        if chosen is not None:
            return chosen
        return super().position_terran(count)

    def position_protoss(self, count) -> Optional[Any]:
        from sc2bench_env.backends.sharpy.races.protoss import ProtossGridBuilding

        return ProtossGridBuilding.position_protoss(self, count)

    def _defensive_position(self):
        solver = self.building_solver
        if solver is None:
            return None
        buildings = self.ai.structures
        reserved = set(getattr(solver, "structure_target_move_location", {}).values())
        addon_spots = set(getattr(solver, "free_addon_locations", ()) or ())
        wall = set(getattr(solver, "wall3x3", ()) or ())
        candidates = []
        for point in getattr(solver, "buildings3x3", ()) or ():
            if point in wall or point in reserved or point in addon_spots:
                continue
            if buildings.closer_than(1, point):
                continue
            candidates.append(point)
        if not candidates:
            return None
        ramp_tops = []
        ramp_bottoms = []
        threatened = []
        minerals = []
        townhalls = []
        main = getattr(self.zone_manager, "own_main_zone", None)
        for zone in list(getattr(self.zone_manager, "expansion_zones", []) or []):
            if not getattr(zone, "is_ours", False):
                continue
            center = getattr(zone, "center_location", None)
            if center is not None:
                townhalls.append(center)
            for mineral in getattr(zone, "mineral_fields", []) or []:
                position = getattr(mineral, "position", None)
                if position is not None:
                    minerals.append(position)
            ramp = getattr(zone, "ramp", None)
            top = getattr(ramp, "top_center", None)
            bottom = getattr(ramp, "bottom_center", None)
            if top is not None:
                ramp_tops.append(top)
                if getattr(zone, "is_under_attack", False):
                    threatened.append(top)
            if bottom is not None:
                ramp_bottoms.append(bottom)
        production = [
            structure.position
            for structure in self.ai.structures
            if structure.type_id in {
                UnitTypeId.BARRACKS, UnitTypeId.FACTORY, UnitTypeId.STARPORT,
                UnitTypeId.GATEWAY, UnitTypeId.ROBOTICSFACILITY, UnitTypeId.STARGATE,
            }
        ]
        existing = [
            structure.position
            for structure in self.ai.structures
            if structure.type_id in set(DEFENSE_TYPES.values())
        ]
        natural = None
        if main is not None and getattr(main, "center_location", None) is not None:
            others = [
                hall for hall in townhalls
                if _dist(hall, main.center_location) > 5
            ]
            if others:
                natural = min(others, key=lambda hall: _dist(hall, main.center_location))
        map_center = getattr(getattr(self.ai, "game_info", None), "map_center", None)
        return choose_defense_position(
            self.kind,
            candidates,
            ramp_tops=ramp_tops,
            threatened_ramps=threatened,
            ramp_bottoms=ramp_bottoms,
            minerals=minerals,
            production=production,
            existing=existing,
            townhalls=townhalls,
            map_center=map_center,
            natural=natural,
        )
