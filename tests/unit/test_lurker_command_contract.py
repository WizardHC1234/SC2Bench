"""Lurker commands respect the native movement/weapon form contract."""
from types import SimpleNamespace as NS
import pytest
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.ids.ability_id import AbilityId as A
from sc2.position import Point2
from sharpy.combat import Action,NoAction
from sharpy.combat.group_combat_manager import GroupCombatManager
from tests.unit.test_combat_execution import UnitStub


def test_above_ground_lurker_approaches_without_an_invalid_attack():
    unit=UnitStub(1,U.LURKERMP)
    enemy=UnitStub(2,U.ROACH,position=(6,0))
    result=GroupCombatManager().legal_lurker_action(unit,Action(enemy,True))
    assert result.target==enemy.position and not result.is_attack


def test_burrowed_lurker_keeps_a_valid_unit_attack():
    unit=UnitStub(1,U.LURKERMPBURROWED)
    enemy=UnitStub(2,U.ROACH,position=(6,0))
    action=Action(enemy,True)
    assert GroupCombatManager().legal_lurker_action(unit,action) is action


def test_burrowed_lurker_uses_native_acquisition_instead_of_position_attack():
    unit=UnitStub(1,U.LURKERMPBURROWED)
    assert isinstance(GroupCombatManager().legal_lurker_action(unit,Action(Point2((6,0)),True)),NoAction)


def test_burrowed_lurker_unburrows_before_moving():
    unit=UnitStub(1,U.LURKERMPBURROWED)
    result=GroupCombatManager().legal_lurker_action(unit,Action(Point2((10,0)),False))
    assert result.ability==A.BURROWUP_LURKER


@pytest.mark.parametrize('ability',[A.BURROWDOWN,A.BURROWUP,A.BURROWDOWN_LURKER,A.BURROWUP_LURKER])
def test_native_form_order_is_not_overwritten(ability):
    unit=UnitStub(1,U.LURKERMPBURROWED,orders=[NS(ability=NS(id=ability))])
    assert isinstance(GroupCombatManager().legal_lurker_action(unit,Action(Point2((10,0)),False)),NoAction)
