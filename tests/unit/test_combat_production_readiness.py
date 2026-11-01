"""Warping units must finish before entering any combat controller."""
from types import SimpleNamespace as NS
import pytest
from sc2.ids.unit_typeid import UnitTypeId as U
from sharpy.combat.group_combat_manager import GroupCombatManager
from sc2.ids.ability_id import AbilityId as A
from sc2.position import Point2
from sharpy.combat import Action, NoAction
from sharpy.combat.terran import MicroTanks


@pytest.mark.parametrize('kind',[U.STALKER,U.ZEALOT,U.ARCHON])
def test_incomplete_units_are_not_registered_for_combat(kind):
    combat=GroupCombatManager();combat._tags=[]
    unit=NS(tag=1,type_id=kind,is_ready=False)
    combat.add_unit(unit)
    assert combat.tags==[]
    unit.is_ready=True
    combat.add_unit(unit)
    assert combat.tags==[1]


def test_no_ready_members_clear_the_registration_instead_of_leaking_to_next_mission():
    combat=GroupCombatManager();combat._tags=[1]
    combat.ai=None
    combat.cache=NS(by_tag=lambda _:NS(tag=1,is_ready=False))
    combat.execute(None)
    assert combat.tags==[]


@pytest.mark.parametrize('ability',[A.SIEGEMODE_SIEGEMODE,A.UNSIEGE_UNSIEGE])
def test_default_tank_micro_does_not_repeat_or_interrupt_an_active_transform(ability):
    unit=NS(orders=[NS(ability=NS(id=ability))])
    assert isinstance(MicroTanks().unit_solve_combat(unit,Action(Point2((10,10)),False)),NoAction)
