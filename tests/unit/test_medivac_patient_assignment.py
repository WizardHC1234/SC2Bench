"""One active healer per patient; retain useful existing healing."""
from types import SimpleNamespace as NS
import numpy as np
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.ids.ability_id import AbilityId as A
from sc2.units import Units
from sc2.position import Point2
from sharpy.combat import Action, NoAction, MoveType
from sc2bench_env.backends.sharpy.micro import MicroMedivacsSupport
from tests.unit.test_combat_execution import UnitStub


def setup():
    med=[UnitStub(i,U.MEDIVAC,energy=100,orders=[]) for i in (1,2)]
    patients=[UnitStub(i,U.MARINE,position=(i-3,0),health_percentage=.2,
                       is_biological=True) for i in (3,4)]
    ai=NS(time=0,state=NS(game_loop=1),units=med+patients)
    ai._distance_units_to_pos=lambda units,p:np.array([u.distance_to(p) for u in units])
    micro=MicroMedivacsSupport();micro.ai=ai;micro.move_type=MoveType.Assault
    micro.enemies_near_by=[];micro.group=NS(ground_units=Units(patients,ai))
    micro._escort_point=lambda *_:Point2((0,0))
    return micro,med,patients


def test_simultaneous_healers_choose_different_patients():
    micro,med,patients=setup()
    first=micro.unit_solve_combat(med[0],Action(None,False))
    second=micro.unit_solve_combat(med[1],Action(None,False))
    assert first.target is patients[0] and second.target is patients[1]
    assert isinstance(micro.unit_solve_combat(med[0],Action(None,False)),NoAction)


def test_existing_healer_claim_is_respected():
    micro,med,patients=setup()
    med[0].orders=[NS(ability=NS(id=A.MEDIVACHEAL_HEAL),target=patients[0].tag)]
    assert micro.unit_solve_combat(med[1],Action(None,False)).target is patients[1]
    assert isinstance(micro.unit_solve_combat(med[0],Action(None,False)),NoAction)


def test_no_duplicate_cast_when_only_patient_is_already_being_healed():
    micro,med,patients=setup()
    micro.group.ground_units=Units(patients[:1],micro.ai)
    med[0].orders=[NS(ability=NS(id=A.MEDIVACHEAL_HEAL),target=patients[0].tag)]
    assert micro.unit_solve_combat(med[1],Action(None,False)).ability is None


def test_unacknowledged_patient_claim_expires_next_frame():
    micro,med,patients=setup()
    micro.unit_solve_combat(med[0],Action(None,False))
    micro.ai.state.game_loop+=1
    assert micro.unit_solve_combat(med[1],Action(None,False)).target is patients[0]
