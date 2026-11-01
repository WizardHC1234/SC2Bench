"""Available targets must survive building upgrades without inventing morph sources."""
import pytest

from sc2bench_env.runtime.observation_builder import available_targets
from sc2bench_env.catalog.prerequisites import prerequisite_keys
from sc2bench_env.backends.sharpy.races.zerg import ZergAdapter


@pytest.mark.parametrize("townhall", ["orbital_command","planetary_fortress"])
def test_terran_upgraded_townhall_keeps_workers_gas_and_engineering_bay_available(townhall):
    targets=available_targets(race="terran",building={townhall:{"completed":1},"barracks":{"completed":1}})
    assert "scv" in targets["train"]
    assert {"refinery","engineering_bay"}<=set(targets["build"])
    assert "orbital_command" not in targets["morph_townhall"]
    assert "planetary_fortress" not in targets["morph_townhall"]


def test_unfinished_upgraded_townhall_does_not_supply_completed_technology():
    targets=available_targets(race="terran",building={"orbital_command":{"completed":0,"under_construction":1}})
    assert "scv" not in targets["train"] and "refinery" not in targets["build"]


@pytest.mark.parametrize("townhall", ["lair","hive"])
def test_zerg_upgraded_townhall_retains_base_technology(townhall):
    targets=available_targets(race="zerg",building={townhall:{"completed":1},"spawning_pool":{"completed":1}},units={"larva":1})
    assert {"extractor","evolution_chamber","hydralisk_den","spire"}<=set(targets["build"])
    assert "queen" in targets["train"]
    assert "lair" not in targets["morph_townhall"]


def test_warp_gates_keep_ground_production_and_cybernetics_core_available():
    targets=available_targets(race="protoss",building={"warpgate":{"completed":2},"cybernetics_core":{"completed":1}},research={"warp_gate":"completed"})
    assert {"zealot","stalker","sentry","adept"}<=set(targets["train"])
    assert "cybernetics_core" in targets["build"]
    assert "warpgate" not in targets["build"]


def test_zerg_morphed_technology_retains_units_without_reusing_consumed_building():
    targets=available_targets(race="zerg",building={"hive":{"completed":1},"greater_spire":{"completed":1},"lurker_den":{"completed":1}},units={"larva":1})
    assert {"mutalisk","corruptor"}<=set(targets["train"])
    assert "hydralisk" not in targets["train"]
    assert "greater_spire" not in targets["build"]
    assert "lurker_den" not in targets["build"]
    assert "nydus_network" not in targets["build"]
    targets=available_targets(race="zerg",building={"hive":{"completed":1},"hydralisk_den":{"completed":1},"lurker_den":{"completed":1}},units={"larva":1})
    assert 'hydralisk' in targets['train'] and 'lurker_den' in targets['build']


def test_executor_uses_same_zerg_hive_technology_equivalence_as_briefing():
    assert ZergAdapter().prerequisite_count_keys("lair")==prerequisite_keys("lair",race="zerg")==("lair","hive")


def test_detailed_production_query_retains_upgraded_zerg_technology():
    from sc2bench_env.interface.observation_text import production_options
    text="\n".join(production_options(
        [{"facility":"hatchery","ready_grounded":1}],
        {"hive":{"completed":1},"lurker_den":{"completed":1},"greater_spire":{"completed":1}},
        race="zerg"))
    assert "hydralisk (tech ready)" not in text
    assert "mutalisk (tech ready)" in text
