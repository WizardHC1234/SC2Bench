"""Platform zone threat is observable weapon range, not Sharpy power balance."""
from math import hypot
from types import SimpleNamespace as NS

import pytest

from sc2bench_env.backends.sharpy.state_reader import (
    _ideal_harvester_total, _known_zone_owner, _visible_weapon_enemies, _worker_assignment,
    _zone_has_visible_weapon_threat, read_snapshot,
)


def unit(x=0, y=0, **attrs):
    result = NS(x=x, y=y, is_visible=True, is_memory=False, is_snapshot=False,
                is_hallucination=False, is_ready=True, health=100, is_flying=False,
                ground_range=5, air_range=0, type_id=NS(name="MARINE"))
    result.__dict__.update(attrs)
    if "can_attack" not in attrs:
        result.can_attack = result.ground_range > 0 or result.air_range > 0
    result.distance_to = lambda other: hypot(result.x - other.x, result.y - other.y)
    result.target_in_range = lambda other: (
        (result.air_range if other.is_flying else result.ground_range) > 0
        and result.distance_to(other) <= (
            result.air_range if other.is_flying else result.ground_range))
    return result


VALUES = NS(real_range=lambda attacker, target:
            attacker.air_range if target.is_flying else attacker.ground_range)


def threat(own, enemies, values=VALUES):
    visible = _visible_weapon_enemies(NS(all_enemy_units=enemies))
    return _zone_has_visible_weapon_threat(NS(our_units=own), visible, values)


@pytest.mark.parametrize("control", ["ours", "neutral", "enemy"])
def test_empty_zones_ignore_sharpy_power_heuristic(control):
    zone = NS(our_units=[], is_under_attack=True, control=control)
    assert not _zone_has_visible_weapon_threat(zone, [unit()], VALUES)


def test_known_owner_uses_evidence_not_sharpy_presumed_control():
    assert _known_zone_owner(NS(our_townhall=object(), enemy_townhall=None,
                                is_ours=False, is_enemys=False)) == "self"
    assert _known_zone_owner(NS(our_townhall=None, enemy_townhall=object(),
                                is_ours=False, is_enemys=False)) == "enemy"
    assert _known_zone_owner(NS(our_townhall=None, enemy_townhall=None,
                                is_ours=False, is_enemys=True)) == "unconfirmed"


@pytest.mark.parametrize("attrs", [
    {"is_visible": False}, {"is_memory": True}, {"is_snapshot": True},
    {"is_hallucination": True}, {"is_ready": False}, {"health": 0},
    {"type_id": NS(name="PHOTONCANNON"), "is_powered": False},
])
def test_unobserved_or_inactive_enemy_does_not_raise_threat(attrs):
    assert not threat([unit()], [unit(**attrs)])


def test_ground_air_and_support_weapons():
    assert not threat([unit(is_flying=True)], [unit(air_range=0)])
    assert threat([unit(is_flying=True)], [unit(air_range=5)])
    assert not threat([unit()], [unit(ground_range=0, air_range=0)])


@pytest.mark.parametrize("name", ["RAVEN", "DISRUPTOR", "ORACLESTASISTRAP", "LURKERMP"])
def test_sharpy_spell_or_inactive_form_range_is_not_a_weapon(name):
    # Deliberately generous UnitValues override: spell reach is not a weapon.
    values = NS(real_range=lambda attacker, target: 9)
    assert not threat([unit()], [unit(type_id=NS(name=name), can_attack=False)], values)


@pytest.mark.parametrize("name", ["CARRIER", "WIDOWMINEBURROWED"])
def test_automatic_attack_with_missing_weapon_metadata(name):
    assert threat([unit()], [unit(type_id=NS(name=name), can_attack=False)])


def test_range_boundary_and_clear_without_relative_power_or_ownership():
    own = [unit()]
    assert not threat(own, [unit(x=5.01)])
    assert threat(own, [unit(x=5)])
    assert not threat(own, [])
    # The same direct range predicate works without Sharpy special-range data.
    assert threat(own, [unit(x=4)], values=None)


def test_ready_powered_static_weapon_threatens_buildings():
    assert threat([unit(is_structure=True)], [
        unit(x=2, type_id=NS(name="PHOTONCANNON"), is_powered=True)])


class UnitList(list):
    @property
    def ready(self):
        return UnitList(item for item in self if item.is_ready)


def test_snapshot_rows_and_wait_share_threat_and_clear_on_next_read():
    own = unit()
    zone = NS(center_location=NS(x=0, y=0), our_units=[own],
              known_enemy_units=[], is_ours=False, is_enemys=False,
              is_under_attack=True)  # Cross-zone enemy is absent from this list.
    empty = NS(center_location=NS(x=20, y=20), our_units=[], is_under_attack=True)
    ai = NS(structures=UnitList(), units=UnitList(), townhalls=UnitList(),
            gas_buildings=UnitList(), zone_manager=NS(expansion_zones=[zone, empty]),
            knowledge=NS(unit_values=VALUES), all_enemy_units=[unit(x=2)])
    adapter = NS(normalize_unit_name=lambda name: None)
    snapshot, *_ = read_snapshot(ai, adapter)
    assert [row["visible_enemy_weapon_in_range"] for row in snapshot.info["zone_state"]] == [True, False]
    assert snapshot.info["zone_state"][0]["visible_enemy_contents"]["units"] == {"marine": 1}
    assert snapshot.info["zones_under_attack"] == ["zone_0"]
    ai.all_enemy_units = []
    snapshot, *_ = read_snapshot(ai, adapter)
    assert not any(row["visible_enemy_weapon_in_range"] for row in snapshot.info["zone_state"])
    assert not snapshot.info["zones_under_attack"]


def test_economy_uses_game_score_and_separates_worker_saturation():
    ai = NS(structures=UnitList(), units=UnitList(), townhalls=UnitList(),
            gas_buildings=UnitList(), zone_manager=NS(expansion_zones=[]),
            state=NS(upgrades=[], score=NS(collection_rate_minerals=123.5,
                                          collection_rate_vespene=45.0)),
            income_calculator=NS(mineral_income=999, gas_income=999))
    adapter = NS(normalize_unit_name=lambda name: None)
    snapshot, *_ = read_snapshot(ai, adapter)
    assert snapshot.info["mineral_income_per_minute"] == 123.5
    assert snapshot.info["vespene_income_per_minute"] == 45.0
    assert snapshot.info["workers_on_minerals"] is None
    assert snapshot.info["workers_on_vespene"] is None
    assert snapshot.info["workers_other"] is None
    assert snapshot.info["mineral_worker_saturation"] == 0
    assert snapshot.info["vespene_worker_saturation"] == 0

    # Missing engine metadata remains unknown instead of being guessed.
    ai.townhalls = UnitList([NS(is_ready=True)])
    snapshot, *_ = read_snapshot(ai, adapter)
    assert snapshot.info["base_count"] == 1
    assert snapshot.info["mineral_worker_saturation"] is None
    ai.townhalls[0].ideal_harvesters = 16
    ai.gas_buildings = UnitList([NS(is_ready=True, ideal_harvesters=3)])
    snapshot, *_ = read_snapshot(ai, adapter)
    assert snapshot.info["mineral_worker_saturation"] == 16
    assert snapshot.info["vespene_worker_saturation"] == 3

    ai.state.score = None
    del ai.townhalls
    del ai.gas_buildings
    snapshot, *_ = read_snapshot(ai, adapter)
    assert snapshot.info["mineral_income_per_minute"] is None
    assert snapshot.info["vespene_income_per_minute"] is None
    assert snapshot.info["mineral_worker_saturation"] is None
    assert snapshot.info["vespene_worker_saturation"] is None


def test_ideal_harvester_total_uses_only_ready_structures():
    structures = UnitList([
        NS(is_ready=True, ideal_harvesters=16),
        NS(is_ready=False, ideal_harvesters=16),
    ])
    assert _ideal_harvester_total(structures) == 16
    assert _ideal_harvester_total(UnitList()) == 0
    assert _ideal_harvester_total(None) is None


def test_worker_assignment_reports_actual_orders_and_keeps_other_workers():
    mineral = NS(tag=10)
    gas = NS(tag=20)
    workers = [
        NS(is_carrying_vespene=False, is_carrying_minerals=False,
           is_gathering=True, is_returning=False, order_target=10),
        NS(is_carrying_vespene=False, is_carrying_minerals=True,
           is_gathering=False, is_returning=True, order_target=None),
        NS(is_carrying_vespene=False, is_carrying_minerals=False,
           is_gathering=True, is_returning=False, order_target=20),
        NS(is_carrying_vespene=False, is_carrying_minerals=False,
           is_gathering=False, is_returning=False, order_target=None),
    ]
    assignment = _worker_assignment(
        NS(workers=workers, mineral_field=[mineral], gas_buildings=[gas]),
        total_workers=5,
    )
    assert assignment == {
        "workers_on_minerals": 2,
        "workers_on_vespene": 1,
        "workers_other": 2,
    }
