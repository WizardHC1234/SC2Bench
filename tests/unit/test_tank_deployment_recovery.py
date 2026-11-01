"""Failed native deployment must recover locally, without overriding combat."""
from types import SimpleNamespace as NS
import pytest
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.ids.ability_id import AbilityId as A
from sc2.position import Point2
from sharpy.combat import Action, NoAction
from sharpy.combat.group_combat_manager import GroupCombatManager
from tests.unit.test_combat_execution import UnitStub


def scene(errors=()):
    tank = UnitStub(1, U.SIEGETANK, position=(10,10), radius=.875, orders=[])
    other = UnitStub(2, U.SIEGETANKSIEGED, position=(9,10), radius=.875)
    combat = GroupCombatManager()
    combat.ai = NS(state=NS(action_errors=list(errors)), all_own_units=[tank,other],
                   all_enemy_units=[], in_pathing_grid=lambda _: True)
    combat.rules = NS(boundary=lambda p: p.y >= 10)
    return combat, tank, other


def failure(tag=1, ability=388, result=208):
    return NS(unit_tag=tag,ability_id=ability,result=result)


def test_failed_deployment_uses_clear_local_ground_then_allows_deployment():
    combat,tank,other = scene([failure()])
    siege = Action(None,False,A.SIEGEMODE_SIEGEMODE)
    action = combat.legal_deployment_action(tank,siege,Point2((8,10)))
    assert action.position is not None and not action.is_attack and action.ability is None
    assert .5 < tank.distance_to(action.position) <= 4.5
    assert other.distance_to(action.position) >= tank.radius + other.radius + .15
    assert combat.rules.boundary(action.position)
    combat.ai.state.action_errors=[]
    assert combat.legal_deployment_action(tank,siege,Point2((8,10))).position == action.position
    tank.position=action.position
    assert combat.legal_deployment_action(tank,siege,Point2((8,10))) is siege
    assert not combat.ai._bench_siege_relocations


@pytest.mark.parametrize('error',[failure(tag=2),failure(ability=16),failure(result=2)])
def test_other_errors_do_not_move_a_stationed_tank(error):
    combat,tank,_=scene([error]); siege=Action(None,False,A.SIEGEMODE_SIEGEMODE)
    assert combat.legal_deployment_action(tank,siege,Point2((8,10))) is siege


@pytest.mark.parametrize('attack',[True,False])
def test_shots_and_new_movement_keep_priority_over_recovery(attack):
    combat,tank,_=scene([failure()])
    combat.ai._bench_siege_relocations={tank.tag:Point2((12,12))}
    command=Action(Point2((20,20)),attack)
    assert combat.legal_deployment_action(tank,command,command.position) is command
    assert not combat.ai._bench_siege_relocations


def test_successfully_deployed_tank_is_never_released_for_recovery():
    combat,tank,_=scene([failure()]);tank.type_id=U.SIEGETANKSIEGED
    combat.ai._bench_siege_relocations={tank.tag:Point2((12,12))}
    command=NoAction()
    assert combat.legal_deployment_action(tank,command,Point2((8,10))) is command
    assert not combat.ai._bench_siege_relocations


def test_recovery_does_not_choose_unwalkable_or_outside_ground():
    combat,tank,_=scene([failure()]);combat.ai.in_pathing_grid=lambda _:False
    command=Action(None,False,A.SIEGEMODE_SIEGEMODE)
    assert isinstance(combat.legal_deployment_action(tank,command,Point2((8,10))),NoAction)
    assert not combat.ai._bench_siege_relocations


def test_a_new_obstacle_does_not_trap_recovery_at_the_old_destination():
    combat,tank,other=scene([failure()])
    siege=Action(None,False,A.SIEGEMODE_SIEGEMODE)
    first=combat.legal_deployment_action(tank,siege,Point2((8,10)))
    other.position=first.position
    combat.ai.state.action_errors=[]
    second=combat.legal_deployment_action(tank,siege,Point2((8,10)))
    assert second.position != first.position
    assert other.distance_to(second.position) >= tank.radius + other.radius + .15


def test_recovery_keeps_the_future_townhall_footprint_clear():
    from sc2bench_env.backends.sharpy.holding import overlaps_townhall_site
    combat,tank,_=scene([failure()])
    combat.rules.reserved_base_center=Point2((12,10))
    result=combat.legal_deployment_action(tank,Action(None,False,A.SIEGEMODE_SIEGEMODE),Point2((12,10)))
    assert result.position is not None
    assert not overlaps_townhall_site(result.position,combat.rules.reserved_base_center,tank.radius)
