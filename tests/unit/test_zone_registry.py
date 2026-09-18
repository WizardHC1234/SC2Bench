"""Unit tests for stable ZoneRegistry."""

from __future__ import annotations

from sc2bench_env.backends.sharpy.zones import ZoneRegistry
from types import SimpleNamespace as NS


class Point(tuple):
    def __new__(cls, x, y):
        return super().__new__(cls, (x, y))
    x = property(lambda self: self[0])
    y = property(lambda self: self[1])
    def distance_to(self, other):
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** .5


def test_zone_ids_stable_across_reorder() -> None:
    registry = ZoneRegistry()
    first = registry.sync_from_centers([(10.0, 20.0), (30.5, 40.5), (1.0, 1.0)])
    assert first == ["zone_0", "zone_1", "zone_2"]

    # Same centers in a different order must keep the same ids.
    second = registry.sync_from_centers([(1.0, 1.0), (10.0, 20.0), (30.5, 40.5)])
    assert second == ["zone_2", "zone_0", "zone_1"]
    assert registry.center_for("zone_0") == (10.0, 20.0)


def test_new_center_gets_new_id() -> None:
    registry = ZoneRegistry()
    registry.sync_from_centers([(0.0, 0.0)])
    ids = registry.sync_from_centers([(0.0, 0.0), (5.0, 5.0)])
    assert ids == ["zone_0", "zone_1"]


def test_roles_use_coordinates_and_remain_fixed_across_reorder_and_ownership():
    registry = ZoneRegistry()
    zones = [NS(center_location=Point(x, 0)) for x in (0, 10, 40, 50)]
    manager = NS(own_natural=zones[1], enemy_natural=zones[2],
                 enemy_start_location_found=True, found_enemy_start=Point(50, 0))
    ai = NS(start_location=Point(0, 0), enemy_start_locations=[Point(50, 0)])
    registry.sync_roles(ai, manager, list(reversed(zones)))
    assert [registry.role_for_center(zone.center_location.x, 0) for zone in zones] == [
        "own_main", "own_natural", "enemy_natural", "enemy_main"]
    # Ownership/list changes do not participate in role calculation.
    registry.sync_roles(ai, manager, [zones[2], zones[0], zones[3], zones[1]])
    assert registry.role_for_center(0, 0) == "own_main"
    assert registry.role_for_center(50, 0) == "enemy_main"


def test_multispawn_enemy_roles_are_withheld_until_start_is_confirmed():
    registry = ZoneRegistry()
    zones = [NS(center_location=Point(x, 0)) for x in (0, 10, 40, 50, 80)]
    ai = NS(start_location=Point(0, 0),
            enemy_start_locations=[Point(50, 0), Point(80, 0)])
    manager = NS(own_natural=zones[1], enemy_natural=zones[2],
                 enemy_start_location_found=False, found_enemy_start=None)
    registry.sync_roles(ai, manager, zones)
    assert registry.role_for_center(50, 0) == "other_expansion"
    assert registry.role_for_center(80, 0) == "other_expansion"
    manager.enemy_start_location_found = True
    manager.found_enemy_start = Point(50, 0)
    registry.sync_roles(ai, manager, zones)
    assert registry.role_for_center(50, 0) == "enemy_main"
    assert registry.role_for_center(40, 0) == "enemy_natural"
    assert registry.role_for_center(80, 0) == "other_expansion"
    registry.reset()
    assert registry.role_for_center(50, 0) == "other_expansion"
