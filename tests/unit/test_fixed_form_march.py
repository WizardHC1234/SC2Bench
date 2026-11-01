"""Formation and transport movement respect deployed and burrowed forms."""
from types import SimpleNamespace as NS
import pytest
from sc2.ids.ability_id import AbilityId as A
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.position import Point2
from tests.unit.test_combat_execution import UnitStub, mission


@pytest.mark.parametrize('kind,ability',[(U.SIEGETANKSIEGED,A.UNSIEGE_UNSIEGE),
    (U.OBSERVERSIEGEMODE,A.MORPH_OBSERVERMODE),(U.LURKERMPBURROWED,A.BURROWUP_LURKER)])
def test_direct_march_releases_fixed_form_before_moving(kind,ability):
    unit=UnitStub(1,kind)
    act=mission(own=[unit])
    act._move_mobile(unit,Point2((10,10)))
    assert unit.commands and unit.commands[-1][0]!='move'
    assert unit.commands[-1][0]==ability
    unit.commands.clear();unit.orders=[NS(ability=NS(id=ability))]
    act._move_mobile(unit,Point2((10,10)))
    assert not unit.commands


def test_mobile_march_preserves_destination_and_already_sent_actions():
    unit=UnitStub(1,U.MARINE);act=mission(own=[unit]);point=Point2((10,10))
    act._move_mobile(unit,point)
    assert unit.commands[-1]==('move',point)
    unit.commands.clear();act.ai.unit_tags_received_action={1}
    act._move_mobile(unit,point)
    assert not unit.commands


def test_march_does_not_interrupt_a_tank_already_transforming():
    unit=UnitStub(1,U.SIEGETANK,orders=[NS(ability=NS(id=A.SIEGEMODE_SIEGEMODE))])
    act=mission(own=[unit]);act._move_mobile(unit,Point2((10,10)))
    assert not unit.commands


@pytest.mark.parametrize('kind,ability', [
    (U.VIKINGFIGHTER,A.MORPH_VIKINGASSAULTMODE),
    (U.VIKINGASSAULT,A.MORPH_VIKINGFIGHTERMODE),
    (U.HELLION,A.MORPH_HELLBAT),
    (U.HELLIONTANK,A.MORPH_HELLION),
    (U.THOR,A.MORPH_THORHIGHIMPACTMODE),
    (U.THORAP,A.MORPH_THOREXPLOSIVEMODE),
])
def test_combat_and_march_preserve_an_active_form_change(kind, ability):
    from sc2bench_env.backends.sharpy.micro import MissionMicroRules
    from sharpy.combat import Action
    from sharpy.combat.action import NoAction
    unit=UnitStub(1,kind,orders=[NS(ability=NS(id=ability))])
    act=mission(own=[unit]);act._move_mobile(unit,Point2((10,10)))
    assert not unit.commands
    rules=MissionMicroRules()
    assert isinstance(rules.guard_action(unit,Action(None,False,ability)),NoAction)
    unit.orders=[]
    command=Action(None,False,ability)
    assert rules.guard_action(unit,command) is command
