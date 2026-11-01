"""Melee focus fire must apply the same target validity as ranged fire."""
from types import SimpleNamespace as NS
import pytest
from sc2.data import Race
from sc2.position import Point2
from sc2.units import Units
from sc2.ids.unit_typeid import UnitTypeId as U
from sharpy.combat import Action,MoveType,MicroStep
from sharpy.combat.default_micro_methods import DefaultMicroMethods


@pytest.mark.parametrize('flags',[
    {'is_memory':True},{'is_snapshot':True},{'is_hallucination':True},
    {'can_be_attacked':False},{}])
def test_melee_never_targets_historical_or_untargetable_contacts(flags):
    enemy=NS(tag=2,type_id=U.QUEENBURROWED,is_memory=False,is_snapshot=False,
        is_hallucination=False,can_be_attacked=True,is_flying=False,
        position=Point2((1,0)),shield_health_percentage=.5,health=100)
    enemy.__dict__.update(flags)
    unit=NS(position=Point2((0,0)),is_structure=False)
    unit.distance_to=lambda other:unit.position.distance_to(other.position)
    step=NS(unit_values=NS(ground_range=lambda _:1,real_range=lambda *_:2),
        cache=NS(enemy_in_range=lambda *_:Units([enemy],None)),last_targeted=lambda _:None,
        knowledge=NS(enemy_race=Race.Zerg),move_type=MoveType.Assault,focus_fired={})
    step.is_target=lambda target:MicroStep.is_target(step,target)
    command=Action(Point2((5,0)),True)
    result=DefaultMicroMethods.melee_focus_fire(step,unit,command,None)
    if flags:assert result is command
    else:assert result.target is enemy
