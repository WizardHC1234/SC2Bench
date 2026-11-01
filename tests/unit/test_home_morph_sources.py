"""Home station orders are not production work or outbound assignments."""
from types import SimpleNamespace as NS
import pytest
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.ids.ability_id import AbilityId as A
from sharpy.managers.core.roles import UnitTask as R
from sc2bench_env.backends.sharpy.combat_styles import unit_available_for_background
from tests.unit.test_combat_execution import UnitStub


@pytest.mark.parametrize('kind',[U.ZERGLING,U.ROACH,U.HYDRALISK,U.CORRUPTOR,U.HIGHTEMPLAR,U.DARKTEMPLAR])
@pytest.mark.parametrize('order',[A.HOLDPOSITION_HOLD,A.HOLDPOSITION,A.STOP_STOP])
def test_stationed_home_units_remain_morph_sources(kind,order):
    unit=UnitStub(1,kind,orders=[NS(ability=NS(id=order))])
    ai=NS(units=[unit],all_enemy_units=[],bench_group0_tags={1},bench_combat_tags=set(),
          roles=NS(is_in_role=lambda role,u:role==R.Reserved))
    assert unit_available_for_background(unit,ai,require_group0=True)
    ai.bench_combat_tags={1}
    assert not unit_available_for_background(unit,ai,require_group0=True)
    ai.bench_combat_tags=set();ai.bench_group0_tags=set()
    assert not unit_available_for_background(unit,ai,require_group0=True)


def test_a_station_order_does_not_make_a_fighting_unit_available_for_morph():
    unit=UnitStub(1,U.ROACH,sight_range=9,orders=[NS(ability=NS(id=A.HOLDPOSITION_HOLD))])
    enemy=UnitStub(2,U.MARINE,position=(5,0))
    ai=NS(units=[unit],all_enemy_units=[enemy],bench_group0_tags={1})
    assert not unit_available_for_background(unit,ai,require_group0=True)
    enemy.position=enemy.position.offset((50,0))
    unit.orders=[NS(ability=NS(id=A.MORPHTORAVAGER_RAVAGER))]
    assert not unit_available_for_background(unit,ai,require_group0=True)


def test_stationed_overlord_is_free_but_a_scout_keeps_its_assignment():
    unit=UnitStub(1,U.OVERLORD,orders=[NS(ability=NS(id=A.HOLDPOSITION_HOLD))])
    ai=NS(roles=NS(is_in_role=lambda *_:False))
    assert unit_available_for_background(unit,ai,require_group0=False)
    ai.roles=NS(is_in_role=lambda role,u:role==R.Scouting)
    assert not unit_available_for_background(unit,ai,require_group0=False)
