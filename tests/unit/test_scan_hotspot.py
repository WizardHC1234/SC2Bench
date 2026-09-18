"""Scan the actual Sharpy heat-area coordinate, not an opaque HeatArea target."""

import asyncio
from types import SimpleNamespace

import pytest

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths

_ensure_runtime_paths()
from sc2.ids.ability_id import AbilityId
from sc2.position import Point2
from sharpy.managers.extensions.heat_map import HeatArea, HeatMapManager
from sc2bench_env.backends.sharpy.acts import ActScanZone


def make_act(manager):
    zone = SimpleNamespace(center_location=Point2((40, 50)))
    casts = []

    class Orbital:
        tag = 1
        energy = 100

        def __call__(self, ability, target):
            # Real BurnySC2 rejects HeatArea here; the old implementation must
            # fail the regression even if a permissive stub would accept it.
            assert isinstance(target, Point2)
            casts.append((ability, target))
            return True

    ai = SimpleNamespace(unit_tags_received_action=set(),
                         structures=lambda _: SimpleNamespace(ready=[Orbital()]),
                         zone_registry=SimpleNamespace(resolve_zone=lambda *_: zone))
    act = ActScanZone("zone_15")
    act.ai = ai
    act.zone_manager = SimpleNamespace()
    act.knowledge = SimpleNamespace(get_manager=lambda _: manager)
    return act, zone, casts


def heat_area(zone, center, heat):
    # Skip unrelated terrain/cache initialization, retain the real returned
    # class and the actual selector's public fields and selection algorithm.
    area = HeatArea.__new__(HeatArea)
    area.zone, area.center, area.heat = zone, center, heat
    return area


def test_actual_heat_map_selector_returns_area_and_scan_casts_its_center_once():
    manager = HeatMapManager.__new__(HeatMapManager)
    act, zone, casts = make_act(manager)
    best = heat_area(zone, Point2((43, 57)), 20)
    manager.heat_areas = [heat_area(zone, Point2((42, 51)), 2),
                          heat_area(object(), Point2((100, 100)), 200), best]
    assert manager.get_zones_hotspot([zone]) is best
    assert asyncio.run(act.execute())
    assert casts == [(AbilityId.SCANNERSWEEP_SCAN, best.center)]
    assert act._done and act.failure_reason is None
    assert asyncio.run(act.execute())
    assert len(casts) == 1


def test_actual_heat_map_without_positive_heat_scans_zone_center():
    manager = HeatMapManager.__new__(HeatMapManager)
    act, zone, casts = make_act(manager)
    manager.heat_areas = [heat_area(zone, Point2((43, 57)), 0)]
    assert manager.get_zones_hotspot([zone]) is None
    assert asyncio.run(act.execute())
    assert casts[0][1] == zone.center_location


@pytest.mark.parametrize("hotspot,expected", [
    (Point2((0, 0)), Point2((0, 0))),
    (SimpleNamespace(position=Point2((12, 14))), Point2((12, 14))),
    (SimpleNamespace(center=Point2((12, 14)), position=Point2((15, 16))), Point2((12, 14))),
    (SimpleNamespace(center="bad", position=Point2((12, 14))), Point2((12, 14))),
    (None, Point2((40, 50))),
    (object(), Point2((40, 50))),
    (SimpleNamespace(center=(12, 14)), Point2((40, 50))),
    (Point2((float("nan"), 14)), Point2((40, 50))),
    (SimpleNamespace(center=Point2((12, float("inf")))), Point2((40, 50))),
])
def test_supported_points_and_invalid_hotspot_fallback(hotspot, expected):
    zones_requested = []

    def select(zones):
        zones_requested.extend(zones)
        return hotspot

    act, zone, casts = make_act(SimpleNamespace(get_zones_hotspot=select))
    assert asyncio.run(act.execute())
    assert zones_requested == [zone]
    assert casts == [(AbilityId.SCANNERSWEEP_SCAN, expected)]


def test_missing_heat_map_keeps_zone_center_fallback():
    act, zone, casts = make_act(None)
    assert asyncio.run(act.execute())
    assert casts[0][1] == zone.center_location
