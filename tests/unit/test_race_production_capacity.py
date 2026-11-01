"""Production observations belong to adapters, not universal Terran rules."""

from types import SimpleNamespace as NS

import pytest

from sc2bench_env.backends.sharpy.races.base import RaceAdapter
from sc2bench_env.backends.sharpy.races.terran import TerranAdapter
from sc2bench_env.backends.sharpy.state_reader import _production_capacity
from tests.unit.test_platform_information import building


def test_reader_delegates_without_interpreting_race_or_fields():
    ai = object()
    rows = [{"facility": "test_facility", "capacity": None}]
    calls = []
    adapter = NS(race_name="test_only", read_production_capacity=lambda state: (calls.append(state), rows)[1])
    assert _production_capacity(ai, adapter) is rows
    assert calls == [ai]


@pytest.mark.parametrize("race", ["terran", "protoss", "zerg"])
def test_missing_hook_does_not_invent_terran_capacity(race):
    assert _production_capacity(NS(structures=[]), NS(race_name=race)) is None


def test_base_hook_reports_unknown_not_empty_or_zero():
    assert RaceAdapter.read_production_capacity(NS(), object()) is None


def test_adapter_failure_is_not_masked_as_unknown():
    def broken(_ai):
        raise RuntimeError("controlled observation failure")
    with pytest.raises(RuntimeError, match="controlled observation failure"):
        _production_capacity(object(), NS(read_production_capacity=broken))


def test_real_terran_adapter_uses_attached_addons_and_townhall_aliases():
    ai = NS(structures=[building("BARRACKS", 1, addon=11, orders=[1]),
                        building("BARRACKSREACTOR", 11),
                        building("ORBITALCOMMAND", 2)])
    adapter = TerranAdapter()
    rows = _production_capacity(ai, adapter)
    assert rows == adapter.read_production_capacity(ai)
    by_facility = {row["facility"]: row for row in rows}
    assert by_facility["barracks"]["capacity"] == 2
    assert by_facility["barracks"]["free_slots"] == 1
    assert by_facility["command_center"]["free_slots"] == 1
    assert ai.structures[0].orders == [1]  # observation does not mutate execution
