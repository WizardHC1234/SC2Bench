"""Ready-target prerequisite facts are supplied by the selected adapter."""

from types import SimpleNamespace as NS

import pytest
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.ids.upgrade_id import UpgradeId

from sc2bench_env.backends.sharpy.macro import ActOngoingMacroTasks
from sc2bench_env.backends.sharpy.races.terran import TerranAdapter


class Objects(list):
    @property
    def ready(self):
        return Objects(unit for unit in self if unit.is_ready)

    @property
    def amount(self):
        return len(self)

    def __call__(self, kind):
        return Objects(unit for unit in self if unit.type_id == kind)

    def of_type(self, kinds):
        return Objects(unit for unit in self if unit.type_id in kinds)


def entity(kind, tag, *, ready=True, addon=0):
    return NS(type_id=kind, tag=tag, is_ready=ready, add_on_tag=addon)


def state():
    structures = Objects([entity(U.COMMANDCENTER, 1), entity(U.ORBITALCOMMAND, 2),
                          entity(U.PLANETARYFORTRESS, 3), entity(U.COMMANDCENTER, 4, ready=False),
                          entity(U.SUPPLYDEPOT, 5), entity(U.SUPPLYDEPOTLOWERED, 6),
                          entity(U.SUPPLYDEPOT, 7, ready=False), entity(U.BARRACKS, 8, addon=9),
                          entity(U.BARRACKSTECHLAB, 9), entity(U.BARRACKSTECHLAB, 10),
                          entity(U.FACTORY, 11, ready=False)])
    return NS(structures=structures, townhalls=structures.of_type([U.COMMANDCENTER, U.ORBITALCOMMAND, U.PLANETARYFORTRESS]),
              units=Objects([entity(U.MARINE, 12), entity(U.MARINE, 13, ready=False)]),
              state=NS(upgrades={UpgradeId.STIMPACK}))


@pytest.mark.parametrize("target,expected", [("command_center", 3), ("orbital_command", 1),
    ("planetary_fortress", 1), ("supply_depot", 2), ("barracks", 1),
    ("barracks_techlab", 1), ("factory", 0), ("marine", 1),
    ("stimpack", 1), ("combat_shield", 0), ("not_a_target", 0)])
def test_existing_ready_target_rules(target, expected):
    assert TerranAdapter().count_ready(state(), target) == expected


def test_townhall_count_fallback_without_townhalls_collection():
    ai = state()
    del ai.townhalls
    assert TerranAdapter().count_ready(ai, "command_center") == 3


def test_macro_delegates_to_adapter_without_overriding_answer():
    macro = ActOngoingMacroTasks([])
    ai = object()
    macro.ai = ai
    calls = []
    macro.adapter = NS(count_ready=lambda received, target: (calls.append((received, target)), 7)[1])
    assert macro._count_ready("test_target") == 7
    assert calls == [(ai, "test_target")]


def test_macro_propagates_ready_count_errors():
    macro = ActOngoingMacroTasks([])
    macro.ai = object()
    def broken(*args):
        raise RuntimeError("controlled ready count failure")
    macro.adapter = NS(count_ready=broken)
    with pytest.raises(RuntimeError, match="controlled ready count failure"):
        macro._count_ready("barracks")
