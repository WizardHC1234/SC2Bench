"""Shared native build-rejection recovery for grid buildings and townhalls."""
from typing import Optional
from sc2.data import ActionResult
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2


class BuildLocationRecovery:
    def _remember_build(self, worker, position):
        data = getattr(self.ai, "_game_data", None)
        unit = data.units.get(self.unit_type.value) if data is not None else None
        ability = getattr(unit, "creation_ability", None)
        ids = {getattr(value, "value", value) for value in
               (getattr(ability, "id", None), getattr(ability, "exact_id", None))}
        self._last_build_order = dict(worker=worker.tag, point=position, abilities=ids-{None})

    def _observe_build_rejection(self):
        order = self._last_build_order
        if order is None:
            return False
        errors = getattr(getattr(self.ai, "state", None), "action_errors", ())
        for error in errors:
            if error.unit_tag != order["worker"] or error.ability_id not in order["abilities"]:
                continue
            try:
                name = ActionResult(error.result).name
            except ValueError:
                continue
            if name not in {"CouldntReachTarget", "CantFindPlacementLocation"} and not name.startswith("CantBuild"):
                continue
            rejected = getattr(self.ai, "bench_rejected_build_locations", None)
            if rejected is None:
                rejected = self.ai.bench_rejected_build_locations = {}
            rejected[(self.unit_type.value, tuple(order["point"]))] = self.ai.time + 10
            worker = self.cache.by_tag(order["worker"])
            if worker is not None:
                for current in worker.orders:
                    target = current.target
                    if (getattr(current.ability.id, "value", None) in order["abilities"]
                            and hasattr(target, "x") and hasattr(target, "y")
                            and abs(target.x-order["point"].x)<.1 and abs(target.y-order["point"].y)<.1):
                        worker.stop()
                        break
            if self.builder_tag == order["worker"]:
                self.clear_worker()
            self._last_build_order = None
            self.waiting_reason = "build_location_rejected"
            return True
        return False

    def _recently_rejected(self, point):
        now = float(getattr(self.ai, "time", 0))
        return any(kind == self.unit_type.value and until > now
                   and point.distance_to_point2(Point2(position)) < 2
                   for (kind, position), until in
                   getattr(self.ai, "bench_rejected_build_locations", {}).items())

    def _pending_build_overlap(self, point):
        """Native placement queries do not reserve builders' future footprints."""
        from sc2.ids.ability_id import AbilityId
        data = getattr(self.ai, '_game_data', None)
        unit = getattr(data, 'units', {}).get(self.unit_type.value)
        radius = getattr(unit, 'footprint_radius', None)
        if not isinstance(radius, (int, float)) or radius <= 0:
            return False
        production = {UnitTypeId.BARRACKS, UnitTypeId.FACTORY, UnitTypeId.STARPORT}
        production_builds = {AbilityId.TERRANBUILD_BARRACKS, AbilityId.TERRANBUILD_FACTORY,
                             AbilityId.TERRANBUILD_STARPORT}
        ours = [(point, radius)]
        if self.unit_type in production:
            ours.append((point.offset((2.5, -.5)), 1.0))
        for structure in getattr(self.ai, "structures", ()):
            if structure.type_id not in production or getattr(structure, "is_flying", False):
                continue
            pad = structure.position.offset((2.5, -.5))
            if any(abs(a.x-pad.x) < ar+1-.001 and abs(a.y-pad.y) < ar+1-.001
                   for a, ar in ours):
                return True
        orders = [(getattr(data, 'abilities', {}).get(action.ability.value), action.target)
                  for action in getattr(self.ai, 'actions', ())]
        orders.extend((order.ability, order.target) for worker in getattr(self.ai, 'workers', ())
                      for order in worker.orders)
        for ability, target in orders:
            proto = getattr(ability, '_proto', None)
            other_radius = getattr(proto, 'footprint_radius', 0)
            if (getattr(proto, 'is_building', False) is not True
                    or not isinstance(other_radius, (int, float)) or other_radius <= 0
                    or not isinstance(target, Point2)):
                continue
            theirs = [(target, other_radius)]
            if getattr(ability, 'exact_id', None) in production_builds:
                theirs.append((target.offset((2.5, -.5)), 1.0))
            if any(abs(a.x-b.x) < ar+br-.001 and abs(a.y-b.y) < ar+br-.001
                   for a, ar in ours for b, br in theirs):
                return True
        return False

    async def _placeable_positions(self, positions):
        allowed = await self.ai.can_place(self.unit_type, positions)
        if self.unit_type in {UnitTypeId.BARRACKS, UnitTypeId.FACTORY, UnitTypeId.STARPORT}:
            pads = [point.offset((2.5, -.5)) for point in positions]
            addon_allowed = await self.ai.can_place(UnitTypeId.SUPPLYDEPOT, pads)
            allowed = [main and addon for main, addon in zip(allowed, addon_allowed)]
        return allowed

    async def checked_position(self, position: Point2, *, max_distance=8) -> Optional[Point2]:
        """Recheck dynamic placement before issuing a build at a cached grid point."""
        self.waiting_reason = None
        allowed = await self._placeable_positions([position])
        if (allowed and allowed[0] and not self._recently_rejected(position)
                and not self._pending_build_overlap(position)):
            return position
        # Relocated structures can invalidate the first cached grid slot. A
        # step-two local search misses valid slots on the other coordinate
        # parity, so query the nearby solver slots before searching off-grid.
        solver = getattr(self, 'building_solver', None)
        grid_name = ('buildings2x2' if self.unit_type in {
            UnitTypeId.PYLON, UnitTypeId.SUPPLYDEPOT} else 'buildings3x3')
        grid = getattr(solver, 'grid', None)
        zone = getattr(grid[position], 'ZoneIndex', None) if grid is not None else None
        candidates = sorted(
            (point for point in getattr(solver, grid_name, ())
             if point != position and (
                 point.distance_to_point2(position) <= max_distance
                 or (zone is not None and getattr(grid[point], 'ZoneIndex', None) == zone))
             and not self._recently_rejected(point)
             and not self._pending_build_overlap(point)),
            key=lambda point: point.distance_to_point2(position),
        )
        if candidates:
            valid = await self._placeable_positions(candidates)
            for point, possible in zip(candidates, valid):
                if possible:
                    return point
        alternative = await self.ai.find_placement(
            self.unit_type, near=position, max_distance=max_distance,
            random_alternative=False, placement_step=2,
            addon_place=self.unit_type in {UnitTypeId.BARRACKS, UnitTypeId.FACTORY, UnitTypeId.STARPORT},
        )
        if alternative is not None and (self._recently_rejected(alternative)
                                        or self._pending_build_overlap(alternative)):
            alternative = None
            for offset in ((4,0),(-4,0),(0,4),(0,-4),(4,4),(-4,-4)):
                candidate = await self.ai.find_placement(
                    self.unit_type, near=position.offset(offset), max_distance=4,
                    random_alternative=False, placement_step=2,
                    addon_place=self.unit_type in {UnitTypeId.BARRACKS,UnitTypeId.FACTORY,UnitTypeId.STARPORT},
                )
                if (candidate is not None and not self._recently_rejected(candidate)
                        and not self._pending_build_overlap(candidate)
                        and candidate.distance_to_point2(position) <= max_distance):
                    alternative = candidate
                    break
        if alternative is None:
            self.waiting_reason = "build_location_rejected" if self._recently_rejected(position) else "no_build_location"
            self.clear_worker()
        return alternative
