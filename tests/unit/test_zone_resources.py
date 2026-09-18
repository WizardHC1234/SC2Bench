"""Initial resource baselines, fog memory, gas transitions and stable IDs."""
from types import SimpleNamespace as NS

import pytest

from sc2bench_env.backends.sharpy.zones import ZoneRegistry
from sc2bench_env.backends.sharpy.zone_resources import ZoneResourceTracker


class Point(tuple):
    def __new__(cls, x, y):
        return super().__new__(cls, (x, y))

    x = property(lambda self: self[0])
    y = property(lambda self: self[1])


def node(x, kind="minerals", amount=1800, **attrs):
    result = NS(position=Point(x, 0), is_mineral_field=kind == "minerals",
                is_vespene_geyser=kind == "vespene", mineral_contents=amount,
                vespene_contents=amount, is_visible=True, is_memory=False,
                is_snapshot=False, is_ready=True)
    result.__dict__.update(attrs)
    return result


def fixture(resources, time=0):
    zone = NS(center_location=Point(0, 0))
    ai = NS(time=time, expansion_locations_dict={zone.center_location: resources},
            mineral_field=[u for u in resources if u.is_mineral_field],
            vespene_geyser=[u for u in resources if u.is_vespene_geyser],
            gas_buildings=[], enemy_structures=[], is_visible=lambda point: True)
    return ai, zone


@pytest.mark.parametrize("slots", [0, 1, 2, 3, 4])
def test_actual_geyser_count_and_all_geysers_not_owned_only(slots):
    resources = [node(1)] + [node(10 + i, "vespene", 2250) for i in range(slots)]
    ai, zone = fixture(resources)
    result = ZoneResourceTracker().read_zone(ai, zone)
    assert result["minerals_remaining"] == result["minerals_initial"] == 1800
    assert result["vespene_remaining"] == result["vespene_initial"] == slots * 2250
    assert result["geyser_slots"] == result["available_geyser_slots"] == slots
    assert result["owned_gas_structure_count"] == 0


def test_baseline_survives_mining_and_disappearance_in_vision():
    mineral = node(1, amount=900)
    gas = node(10, "vespene", 2250)
    ai, zone = fixture([mineral, gas])
    tracker = ZoneResourceTracker()
    tracker.read_zone(ai, zone)
    mineral.mineral_contents = 600
    ai.time = 10
    result = tracker.read_zone(ai, zone)
    assert result["minerals_remaining"] == 600 and result["minerals_initial"] == 900
    ai.mineral_field = []
    result = tracker.read_zone(ai, zone)
    assert result["minerals_remaining"] == 0 and result["minerals_initial"] == 900


@pytest.mark.parametrize("fog_attr", ["is_visible", "is_snapshot", "is_memory"])
def test_fog_does_not_update_quantities_or_reset_initial(fog_attr):
    mineral = node(1)
    ai, zone = fixture([mineral])
    tracker = ZoneResourceTracker()
    tracker.read_zone(ai, zone)
    setattr(mineral, fog_attr, fog_attr != "is_visible")
    mineral.mineral_contents = 111  # Deliberately leak-like stale/fog value.
    ai.is_visible = lambda point: False
    ai.time = 10
    result = tracker.read_zone(ai, zone)
    assert result["minerals_remaining"] == result["minerals_initial"] == 1800
    assert result["resource_visibility"] == "last_seen"


def test_unseen_initial_stays_unknown_after_later_scout():
    mineral = node(1, is_visible=False, is_snapshot=True)
    gas = node(10, "vespene", is_visible=False, is_snapshot=True)
    ai, zone = fixture([mineral, gas])
    ai.is_visible = lambda point: False
    tracker = ZoneResourceTracker()
    result = tracker.read_zone(ai, zone)
    assert result["minerals_remaining"] is result["minerals_initial"] is None
    assert result["available_geyser_slots"] is None
    assert result["geyser_slots"] == 1 and result["resource_visibility"] == "unknown"
    mineral.is_visible = gas.is_visible = True
    mineral.is_snapshot = gas.is_snapshot = False
    ai.is_visible = lambda point: True
    ai.time = 60
    mineral.mineral_contents = 500
    result = tracker.read_zone(ai, zone)
    assert result["minerals_remaining"] == 500
    assert result["minerals_initial"] is result["vespene_initial"] is None
    assert result["resource_visibility"] == "visible"


def test_first_observation_after_start_is_not_initial_amount():
    ai, zone = fixture([node(1, amount=500)], time=10)
    result = ZoneResourceTracker().read_zone(ai, zone)
    assert result["minerals_remaining"] == 500 and result["minerals_initial"] is None


@pytest.mark.parametrize("kind,prefix", [("minerals", "minerals"), ("vespene", "vespene")])
def test_partial_view_does_not_sum_unknown_as_zero(kind, prefix):
    ai, zone = fixture([node(1, kind), node(2, kind, is_visible=False)])
    ai.is_visible = lambda point: point.x == 1
    result = ZoneResourceTracker().read_zone(ai, zone)
    assert result[f"{prefix}_remaining"] is result[f"{prefix}_initial"] is None
    assert result["resource_visibility"] == "partial"


def test_refinery_replaces_geyser_by_position_and_construction_zero_is_not_depletion():
    gas = node(10, "vespene", 2250)
    ai, zone = fixture([gas])
    tracker = ZoneResourceTracker()
    tracker.read_zone(ai, zone)
    refinery = node(10, "vespene", 0, is_ready=False)
    ai.gas_buildings = [refinery]
    ai.vespene_geyser = []  # Replaced neutral entity is no longer available.
    ai.time = 20
    result = tracker.read_zone(ai, zone)
    assert result["vespene_remaining"] == result["vespene_initial"] == 2250
    assert result["owned_gas_structure_count"] == 1 and result["available_geyser_slots"] == 0
    refinery.is_ready = True
    refinery.vespene_contents = 2000
    result = tracker.read_zone(ai, zone)
    assert result["vespene_remaining"] == 2000 and result["vespene_initial"] == 2250
    # Destroying refinery restores the same node, never counts an extra geyser.
    ai.gas_buildings = []
    ai.vespene_geyser = [gas]
    gas.vespene_contents = 2000
    result = tracker.read_zone(ai, zone)
    assert result["geyser_slots"] == result["available_geyser_slots"] == 1
    assert result["owned_gas_structure_count"] == 0 and result["vespene_initial"] == 2250


def test_enemy_occupation_is_not_available_and_fog_preserves_last_confirmation():
    gas = node(10, "vespene", 2250)
    ai, zone = fixture([gas])
    tracker = ZoneResourceTracker()
    tracker.read_zone(ai, zone)
    enemy = node(10, "vespene", 2100)
    ai.enemy_structures = [enemy]
    ai.time = 20
    result = tracker.read_zone(ai, zone)
    assert result["vespene_remaining"] == 2100
    assert result["available_geyser_slots"] == result["owned_gas_structure_count"] == 0
    enemy.is_visible = gas.is_visible = False
    enemy.vespene_contents = 100
    ai.is_visible = lambda point: False
    ai.time = 30
    result = tracker.read_zone(ai, zone)
    assert result["vespene_remaining"] == 2100 and result["available_geyser_slots"] == 0
    ai.enemy_structures = []
    gas.is_visible = True
    gas.vespene_contents = 2100
    ai.is_visible = lambda point: True
    result = tracker.read_zone(ai, zone)
    assert result["available_geyser_slots"] == 1


def test_zone_identity_and_resource_history_survive_reorder_and_reset():
    registry = ZoneRegistry()
    ai, zone = fixture([node(1, amount=900)])
    registry.sync_from_centers([(0, 0), (30, 30)])
    registry.resources.read_zone(ai, zone)
    registry.sync_from_centers([(30, 30), (0, 0)])
    ai.time = 10
    ai.mineral_field[0].mineral_contents = 500
    result = registry.resources.read_zone(ai, zone)
    assert registry.center_for("zone_0") == (0, 0)
    assert result["minerals_initial"] == 900 and result["minerals_remaining"] == 500
    registry.reset()
    ai.time = 0
    result = registry.resources.read_zone(ai, zone)
    assert result["minerals_initial"] == 500


@pytest.mark.parametrize("kind,field", [("minerals", "mineral_contents"),
                                      ("vespene", "vespene_contents")])
@pytest.mark.parametrize("missing", [True, False], ids=["absent", "null"])
def test_missing_resource_quantity_stays_unknown_until_measured(kind, field, missing):
    resource = node(1, kind)
    if missing:
        delattr(resource, field)
    else:
        setattr(resource, field, None)
    ai, zone = fixture([resource])
    tracker = ZoneResourceTracker()
    result = tracker.read_zone(ai, zone)
    assert result[f"{kind}_remaining"] is result[f"{kind}_initial"] is None
    setattr(resource, field, 700)
    ai.time = 10
    result = tracker.read_zone(ai, zone)
    assert result[f"{kind}_remaining"] == 700
    assert result[f"{kind}_initial"] is None


@pytest.mark.parametrize("kind,field", [("minerals", "mineral_contents"),
                                      ("vespene", "vespene_contents")])
def test_missing_latest_quantity_does_not_erase_last_known_amount(kind, field):
    resource = node(1, kind, 900)
    ai, zone = fixture([resource])
    tracker = ZoneResourceTracker()
    tracker.read_zone(ai, zone)
    setattr(resource, field, None)
    ai.time = 10
    result = tracker.read_zone(ai, zone)
    assert result[f"{kind}_remaining"] == result[f"{kind}_initial"] == 900
    assert result["resource_visibility"] == "last_seen"
