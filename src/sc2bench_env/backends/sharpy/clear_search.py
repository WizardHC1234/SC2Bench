"""Group-scoped map search using public terrain and own enemy sightings only.

Like Sharpy's PlanFinishEnemy, visible buildings take priority over expansion
search. Unit ownership and combat commands remain with ActCombatMission.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from math import ceil, cos, pi, sin
from typing import Optional

from sc2.position import Point2

from sc2bench_env.backends.sharpy.zone_contents import is_currently_visible_enemy


@dataclass(frozen=True)
class SearchTarget:
    key: str
    position: Point2
    kind: str
    zone_id: Optional[str] = None
    tag: Optional[int] = None


class ClearMapSearch:
    """Keep a stable shared objective; search progress survives local combat."""

    def __init__(self):
        self.current: Optional[SearchTarget] = None
        self.route: list[SearchTarget] = []
        self.visited: set[str] = set()
        self._skipped: set[str] = set()
        self.sweeps = 0
        self.phase = "searching"
        self._known: dict[int, SearchTarget] = {}
        self._retry: dict[str, tuple[float, str]] = {}
        self._best_distance = float("inf")
        self._progress_at = 0.0
        self._next_sweep = 0.0
        self._initialized = False
        self._approach: Optional[Point2] = None

    @staticmethod
    def _zone(registry, point):
        centers = [(z, registry.center_for(z)) for z in registry.zone_ids] if registry else []
        return min(centers, key=lambda row: point.distance_to(Point2(row[1])))[0] if centers else None

    def _make_route(self, ai, registry):
        """Expansion centers/surroundings plus a regular public-map search grid.

        The grid also covers non-expansion locations and map edges. Ground
        search projects unwalkable points onto nearby walkable terrain; an air
        group can visit the original points, including space over cliffs.
        """
        points = []
        if registry:
            for zone_id in registry.zone_ids:
                center = registry.center_for(zone_id)
                if center is not None:
                    center = Point2(center)
                    points.extend((center.offset(offset), zone_id) for offset in
                                  ((0, 0), (8, 0), (-8, 0), (0, 8), (0, -8)))
        area = getattr(getattr(ai, "game_info", None), "playable_area", None)
        if area is not None:
            nx, ny = max(1, ceil(area.width / 10)), max(1, ceil(area.height / 10))
            for ix in range(nx):
                for iy in range(ny):
                    point = Point2((area.x + (ix + .5) * area.width / nx,
                                    area.y + (iy + .5) * area.height / ny))
                    points.append((point, self._zone(registry, point)))
        seen = set()
        for point, zone_id in points:
            tile = point.rounded
            if area is not None and not (area.x <= tile.x < area.x + area.width
                                         and area.y <= tile.y < area.y + area.height):
                continue
            key = f"point:{point.x:.1f}:{point.y:.1f}"
            if key not in seen:
                self.route.append(SearchTarget(key, point, "search_point", zone_id))
                seen.add(key)
        self._initialized = True

    def _observe(self, ai, registry):
        visible = {int(unit.tag): unit for unit in getattr(ai, "enemy_structures", ())
                   if is_currently_visible_enemy(unit)}
        # Shared last-sighting records are populated only from own vision.
        tracker = getattr(registry, "enemies", None)
        if tracker is not None:
            for record in tracker.structure_sightings():
                self._known.setdefault(record.tag, SearchTarget(
                    f"structure:{record.tag}", Point2(record.position),
                    "last_seen_structure", record.zone_id, record.tag,
                ))
        for tag, unit in visible.items():
            self._known[tag] = SearchTarget(
                f"structure:{tag}", unit.position, "visible_structure",
                self._zone(registry, unit.position), tag,
            )
        for tag, target in list(self._known.items()):
            if tag not in visible:
                if ai.is_visible(target.position):
                    del self._known[tag]
                else:
                    self._known[tag] = replace(target, kind="last_seen_structure")
        return visible

    def _block(self, target, now, reason):
        self._retry[target.key] = (now + 30, reason)
        if target.kind == "search_point":
            self._skipped.add(target.key)
        if self.current and self.current.key == target.key:
            self.current = None
            self._approach = None

    async def _reachable(self, ai, target, members, can_transport, attack_range):
        ground = [unit for unit in members if not unit.is_flying]
        if not ground:
            return target.position
        pathable = getattr(ai, "in_pathing_grid", None)
        origin = min(ground, key=lambda unit: unit.distance_to(target.position)).position
        max_offset = attack_range if target.tag is not None else 7.0
        points = [target.position]
        for radius in (2, 4, 6, max_offset):
            if radius <= 0 or radius > max_offset:
                continue
            points.extend(target.position.offset((radius * cos(i * pi / 4), radius * sin(i * pi / 4)))
                          for i in range(8))
        area = getattr(getattr(ai, "game_info", None), "playable_area", None)
        if area is not None:
            # BurnySC2 rounds coordinates before indexing its pixel maps.
            points = [point for point in points if area.x <= point.rounded.x < area.x + area.width
                      and area.y <= point.rounded.y < area.y + area.height]
        points = [point for point in points if not callable(pathable) or pathable(point)]
        points.sort(key=lambda point: (point.distance_to(target.position), point.distance_to(origin)))
        query = getattr(getattr(ai, "client", None), "query_pathing", None)
        for point in points:
            if can_transport or origin.distance_to(point) < 1 or not callable(query):
                return point
            distance = await query(origin, point)
            if distance is not None and float(distance) > 0:
                return point
        return None

    async def update(self, ai, registry, members, can_hit, attack_range, *,
                     in_contact=False, can_transport=False, check_weapons=True):
        now = float(ai.time)
        members = list(members)
        visible = self._observe(ai, registry)
        if not self._initialized:
            self._make_route(ai, registry)
        self._retry = {key: row for key, row in self._retry.items() if row[0] > now}
        if not members:
            return self._approach
        origin = Point2((sum(unit.position.x for unit in members) / len(members),
                         sum(unit.position.y for unit in members) / len(members)))
        # Seeing a search point counts as inspecting it, not clearing the map.
        self.visited.update(point.key for point in self.route if ai.is_visible(point.position))
        candidates = []
        for target in self._known.values():
            unit = visible.get(target.tag)
            if unit is not None:
                reason = None
                if getattr(unit, "can_be_attacked", True) is False:
                    reason = "detection_required" if getattr(unit, "is_cloaked", False) or getattr(unit, "is_burrowed", False) else "target_not_attackable"
                elif check_weapons and not any(can_hit(member, unit) for member in members):
                    reason = "cannot_attack_air" if unit.is_flying else "cannot_attack_ground"
                if reason:
                    self._block(target, now, reason)
                    continue
                # Capability/detection changes should take effect immediately.
                if self._retry.get(target.key, (0, ""))[1] in {
                    "cannot_attack_air", "cannot_attack_ground", "detection_required", "target_not_attackable",
                }:
                    self._retry.pop(target.key, None)
            if target.key not in self._retry:
                candidates.append(target)
        candidates.sort(key=lambda target: (
            target.kind != "visible_structure",
            not (self.current and target.key == self.current.key),
            origin.distance_to(target.position), target.key,
        ))
        if in_contact and self.current is not None and self.current.key not in self._retry:
            self.phase = "engaging"
            self._progress_at = now
            return self._approach or self.current.position
        if self.route and len(self.visited | self._skipped) == len(self.route) and now >= self._next_sweep:
            self.sweeps += 1
            self.visited.clear()
            self._skipped.clear()
            self._next_sweep = now + 30
            self.visited.update(point.key for point in self.route if ai.is_visible(point.position))
        points = [point for point in self.route if point.key not in self.visited
                  and point.key not in self._skipped and point.key not in self._retry]
        points.sort(key=lambda target: (
            not (self.current and target.key == self.current.key), origin.distance_to(target.position), target.key,
        ))
        candidates.extend(points)
        previous = self.current
        for target in candidates:
            same = previous is not None and previous.key == target.key
            if same:
                distance = min(unit.distance_to(target.position) for unit in members)
                if distance < self._best_distance - 1:
                    self._best_distance, self._progress_at = distance, now
                if now - self._progress_at >= 20:
                    self._block(target, now, "movement_stalled")
                    continue
            if same and target.position == previous.position and self._approach is not None:
                approach = self._approach
            else:
                enemy = visible.get(target.tag)
                reach = max((attack_range(unit, enemy) for unit in members), default=0) if enemy else 7
                approach = await self._reachable(ai, target, members, can_transport, max(0, reach - .5))
            if approach is None:
                self._block(target, now, "unreachable")
                continue
            if not same or target.position != previous.position:
                self._best_distance = min(unit.distance_to(target.position) for unit in members)
                self._progress_at = now
            self.current, self._approach = target, approach
            self.phase = "engaging" if in_contact else "searching"
            return approach
        self.current, self._approach = None, None
        self.phase = "waiting"
        return None

    def observation(self):
        row = {"visited": len(self.visited), "total": len(self.route), "sweeps": self.sweeps}
        if self._skipped - self.visited:
            row["skipped"] = len(self._skipped - self.visited)
        if self.current:
            row.update(target_kind=self.current.kind,
                       position=[round(self.current.position.x, 1), round(self.current.position.y, 1)])
        if self._retry:
            row["blocked"] = dict(Counter(reason for _, reason in self._retry.values()))
        return row
