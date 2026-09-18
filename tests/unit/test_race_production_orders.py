"""Order decoding delegation and unchanged Terran snapshot facts."""

import hashlib
import json
from dataclasses import asdict
from types import SimpleNamespace as NS

import pytest

from sc2bench_env.backends.sharpy.races.base import RaceAdapter
from sc2bench_env.backends.sharpy.races.terran import TerranAdapter, UNITS
from sc2bench_env.backends.sharpy.state_reader import read_snapshot
from sc2bench_env.interface.action_catalog import targets_for_action
from tests.unit.test_platform_information import building


class Units(list):
    @property
    def ready(self):
        return Units(unit for unit in self if unit.is_ready)


def order(text):
    return NS(ability=NS(id=text))


def snapshot_fixture():
    cc = building("COMMANDCENTER", 1, orders=[order("UPGRADETOORBITAL_ORBITALCOMMAND")])
    depot = building("SUPPLYDEPOT", 2, ready=False)
    barracks = building("BARRACKS", 3, orders=[order("BARRACKSTRAIN_MARINE"), order("BARRACKSTRAIN_MARINE")])
    lab = building("BARRACKSTECHLAB", 4, orders=[order("RESEARCH_STIMPACK")])
    factory = building("FACTORY", 5, orders=[order("FACTORYTRAIN_SIEGETANK")])
    worker = building("SCV", 6, orders=[order("TERRANBUILD_BARRACKS")])
    worker.is_structure = False
    marine = building("MARINE", 7)
    marine.is_structure = False
    return NS(structures=Units([cc, depot, barracks, lab, factory]),
              units=Units([worker, marine]), townhalls=Units([cc]), gas_buildings=Units(),
              state=NS(upgrades=[]), time=42, minerals=300, vespene=100)


def test_terran_snapshot_preserves_counts_and_worker_dispatch():
    snapshot, train, build, research = read_snapshot(snapshot_fixture(), TerranAdapter())
    assert train == {"marine": 2, "siege_tank": 1}
    assert build == {"supply_depot": 1, "orbital_command": 1}
    assert research == {"stimpack": 1}
    assert snapshot.info["workers_en_route"] == {"barracks": 1}
    assert snapshot.units == {"scv": 1, "marine": 1}
    assert snapshot.info["under_construction"] == build


def test_terran_snapshot_matches_before_refactor_fingerprint():
    snapshot = read_snapshot(snapshot_fixture(), TerranAdapter())[0]
    digest = hashlib.sha256(json.dumps(asdict(snapshot), sort_keys=True).encode()).hexdigest()
    assert digest == "30f0c289fcb98b157b768fb056562cca15fb4c818b178cbdc9237937c50dda9b"


@pytest.mark.parametrize("target", sorted(UNITS))
def test_all_train_targets_keep_order_decoding(target):
    unit_type, _producer = UNITS[target]
    assert TerranAdapter().production_order_target(order("TRAIN_" + unit_type.name), None) == ("train", target)


@pytest.mark.parametrize("target", [spec.name for spec in targets_for_action("build") if spec.kind == "building"])
def test_worker_build_targets_keep_order_decoding(target):
    adapter = TerranAdapter()
    command = order("TERRANBUILD_" + target.replace("_", "").upper())
    assert adapter.worker_build_target("scv", command) == target
    assert adapter.worker_build_target("marine", command) is None


@pytest.mark.parametrize("text,expected", [
    ("RESEARCH_SHIELDWALL", ("research", "combat_shield")),
    ("RESEARCH_PUNISHERGRENADES", ("research", "concussive_shells")),
    ("RESEARCH_BANSHEECLOAK", ("research", "cloaking_field")),
    ("RESEARCH_INFANTRYWEAPONS", ("research", "infantry_weapons_1")),
    ("RESEARCH_INFANTRYARMOR", ("research", "infantry_armor_1")),
    ("UPGRADETOPLANETARY_PLANETARYFORTRESS", ("build", "planetary_fortress")),
    ("RESEARCH_UNKNOWN_MARINE", None),  # must not fall through to training
    ("MOVE", None),
])
def test_legacy_text_priority_and_unknown_orders(text, expected):
    assert TerranAdapter().production_order_target(order(text), None) == expected


def test_exact_research_target_wins_over_text_and_is_counted_once():
    adapter = TerranAdapter()
    calls = []
    game_data = object()
    adapter.research_target_from_order = lambda ability, data: (calls.append((ability, data)), "infantry_weapons_3")[1]
    ai = snapshot_fixture()
    ai.units = Units()
    command = order("RESEARCH_INFANTRYWEAPONS")
    ai.structures = Units([building("ENGINEERINGBAY", 9, orders=[command])])
    ai._game_data = game_data
    _snapshot, train, build, research = read_snapshot(ai, adapter)
    assert research == {"infantry_weapons_3": 1}
    assert train == build == {}
    assert calls == [(command.ability, game_data)]


@pytest.mark.parametrize("hooks", [False, True])
def test_unimplemented_decoders_never_fall_back_to_terran(hooks):
    adapter = NS(race_name="test_only", normalize_unit_name=TerranAdapter().normalize_unit_name)
    if hooks:
        adapter.worker_build_target = lambda name, command: RaceAdapter.worker_build_target(adapter, name, command)
        adapter.production_order_target = lambda command, data: RaceAdapter.production_order_target(adapter, command, data)
    snapshot, train, build, research = read_snapshot(snapshot_fixture(), adapter)
    assert train == research == {}
    assert build == {"supply_depot": 1}  # actual observed structure is preserved
    assert snapshot.info["workers_en_route"] == {}


def test_public_reader_uses_adapter_targets_not_terran_hints():
    calls = []
    adapter = NS(normalize_unit_name=lambda name: name.lower(), race_name="test_only",
                 worker_build_target=lambda name, command: "test_building" if name == "scv" else None)
    command = order("MOVE")
    ai = snapshot_fixture()
    ai.structures = Units([building("TESTFACILITY", 9, orders=[command])])
    ai.units[0].orders = [command]
    ai._game_data = object()
    def decode(received, data):
        calls.append((received, data))
        return "train", "test_unit"
    adapter.production_order_target = decode
    snapshot, train, _build, _research = read_snapshot(ai, adapter)
    assert train == {"test_unit": 1}
    assert snapshot.info["workers_en_route"] == {"test_building": 1}
    assert calls == [(command, ai._game_data)]


@pytest.mark.parametrize("hook", ["worker_build_target", "production_order_target"])
def test_decoder_errors_are_not_silently_masked(hook):
    adapter = TerranAdapter()
    def broken(*args):
        raise RuntimeError("controlled decoder failure")
    setattr(adapter, hook, broken)
    with pytest.raises(RuntimeError, match="controlled decoder failure"):
        read_snapshot(snapshot_fixture(), adapter)


def test_unready_facility_orders_are_not_paid_production():
    ai = snapshot_fixture()
    ai.structures = Units([building("BARRACKS", 9, ready=False, orders=[order("TRAIN_MARINE")])])
    _snapshot, train, build, research = read_snapshot(ai, TerranAdapter())
    assert train == research == {}
    assert build == {"barracks": 1}
