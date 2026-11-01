"""Regression for a real 24-worker demand stopping at 22 after gas started."""
from types import SimpleNamespace as NS
import pytest

from sc2bench_env.backends.sharpy.state_reader import _include_hidden_workers, _worker_assignment
from sc2bench_env.backends.sharpy.races.terran import TerranAdapter
from sc2bench_env.backends.base import BackendSnapshot


@pytest.mark.parametrize("race,worker", [("terran", "scv"), ("protoss", "probe"), ("zerg", "drone")])
def test_hidden_workers_restore_completed_inventory_without_counting_mules(race, worker):
    units = {worker: 20, "mule": 2}
    assert _include_hidden_workers(NS(supply_workers=22), NS(race_name=race), units) == 2
    assert units == {worker: 22, "mule": 2}


def test_absolute_training_target_does_not_shrink_when_workers_enter_gas():
    units = {"scv": 20}
    _include_hidden_workers(NS(supply_workers=22), TerranAdapter(), units)
    snapshot = BackendSnapshot(units=units)
    assert TerranAdapter().production_owned_count(snapshot, "train", "scv") + 2 == 24


def test_hidden_gas_assignment_is_bounded_and_does_not_double_count_orders():
    workers = [NS(is_gathering=True, is_returning=False, order_target=10),
               NS(is_gathering=True, is_returning=False, order_target=20)]
    ai = NS(workers=workers, mineral_field=[NS(tag=10)],
            gas_buildings=[NS(tag=20, assigned_harvesters=3)])
    assert _worker_assignment(ai, 4, hidden_workers=2) == {
        "workers_on_minerals": 1, "workers_on_vespene": 3, "workers_other": 0}
    assert _worker_assignment(ai, 5, hidden_workers=3) == {
        "workers_on_minerals": 1, "workers_on_vespene": 3, "workers_other": 1}


@pytest.mark.parametrize("reported", [None, True, -1, float("nan"), float("inf")])
def test_missing_or_invalid_counter_does_not_invent_workers(reported):
    units = {"scv": 8}
    assert _include_hidden_workers(NS(supply_workers=reported), NS(race_name="terran"), units) == 0
    assert units == {"scv": 8}


def test_loaded_workers_already_in_inventory_are_not_added_again():
    units = {"scv": 16}
    assert _include_hidden_workers(NS(supply_workers=16), TerranAdapter(), units) == 0
