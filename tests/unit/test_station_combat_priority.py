"""Holding must not issue a movement order over firing or deployed waiting."""
from types import SimpleNamespace as NS
import asyncio
import pytest
from sc2.position import Point2
from sc2.ids.ability_id import AbilityId as A
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.units import Units
from sharpy.combat import Action, NoAction
from sc2bench_env.backends.sharpy.micro import MissionMicroRules, MicroDefendTanks
from tests.unit.test_combat_execution import UnitStub
from tests.unit.test_combat_micro import _configure_defend_micro
from sc2bench_env.backends.sharpy.combat_styles import at_defend_station


def rules():
    result=MissionMicroRules()
    result.hold_position=True
    result.return_point=Point2((10,10))
    result.boundary=lambda p:p.x<=12
    result.unit_values=NS(real_range=lambda *_:7)
    result.ai=NS(all_enemy_units=[])
    return result


def test_nearby_unit_on_the_ramp_has_not_reached_the_allowed_station_area():
    unit=UnitStub(1,U.SIEGETANK,position=(125.841796875,54.0625))
    micro=NS(move_type=None,rules=NS(hold_position=True,return_point=Point2((127.5,49.5)),
                                   boundary=lambda p:p.y<=53))
    assert unit.distance_to(micro.rules.return_point)<5
    assert not at_defend_station(micro,unit)
    micro.rules.boundary=lambda _:True
    assert at_defend_station(micro,unit)


def test_no_action_does_not_become_move_for_deployed_defender():
    r=rules();tank=UnitStub(1,U.SIEGETANKSIEGED,position=(10,10))
    command=NoAction()
    assert r.guard_action(tank,command) is command


def test_observer_mid_form_change_is_not_released_again_by_the_station_boundary():
    r=rules();r.boundary=lambda _:False
    observer=UnitStub(1,U.OBSERVERSIEGEMODE,position=(15,10),
        orders=[NS(ability=NS(id=A.MORPH_SURVEILLANCEMODE))])
    assert isinstance(r.guard_action(observer,NoAction()),NoAction)


@pytest.mark.parametrize("hold_ability",[A.HOLDPOSITION_HOLD,A.HOLDPOSITION,A.HOLDPOSITION_BATTLECRUISER])
def test_hold_area_stops_idle_shuffling_instead_of_moving_to_exact_center(hold_ability):
    r=rules();unit=UnitStub(1,U.MARINE,position=(11,10),orders=[])
    command=r.guard_action(unit,Action(Point2((10,10)),False))
    assert command.ability==A.HOLDPOSITION_HOLD
    unit.orders=[NS(ability=NS(id=hold_ability))]
    assert isinstance(r.guard_action(unit,Action(Point2((10,10)),False)),NoAction)


def test_defender_can_fire_at_an_in_range_enemy_across_the_leash():
    r=rules();unit=UnitStub(1,U.MARINE,position=(11,10))
    enemy=UnitStub(2,U.MARINE,position=(15,10))
    attack=Action(enemy,True)
    assert r.guard_action(unit,attack) is attack


def test_tank_can_deploy_in_local_fight_before_arriving_at_station():
    tank=UnitStub(1,U.SIEGETANK,position=(10,10))
    enemy=UnitStub(2,U.MARINE,position=(20,10))
    micro=MicroDefendTanks();_configure_defend_micro(micro,Point2((30,30)))
    micro.rules.station_before_deploy=True
    micro.enemies_near_by=Units([enemy],micro.ai)
    micro._local_ground_enemies=lambda unit:NS(exists=True,closest_distance_to=lambda _:unit.distance_to(enemy))
    assert micro.unit_solve_combat(tank,Action(Point2((30,30)),False)).ability==A.SIEGEMODE_SIEGEMODE
    tank.type_id=U.SIEGETANKSIEGED
    assert isinstance(micro.unit_solve_combat(tank,Action(Point2((30,30)),False)),NoAction)


def test_boundary_does_not_unsiege_a_tank_already_fighting_locally():
    r=rules();tank=UnitStub(1,U.SIEGETANKSIEGED,position=(13,10))
    enemy=UnitStub(2,U.MARINE,position=(20,10))
    r.ai.all_enemy_units=[enemy]
    command=NoAction()
    assert r.guard_action(tank,command) is command


@pytest.mark.parametrize("kind",[U.MARINE,U.STALKER,U.ROACH])
@pytest.mark.parametrize("attack",[True,False])
def test_visible_contact_preserves_approach_and_kiting_across_station_boundary(kind,attack):
    r=rules();unit=UnitStub(1,kind,position=(11,10),sight_range=11)
    enemy=UnitStub(2,U.MARINE,position=(20,10))
    r.ai.all_enemy_units=[enemy]
    command=Action(enemy if attack else Point2((14,14)),attack)
    assert r.guard_action(unit,command) is command


def test_nearby_group_vision_allows_back_line_combat():
    r=rules();unit=UnitStub(1,position=(10,10),sight_range=9)
    ally=UnitStub(2,position=(16,10),sight_range=9)
    r.ai.all_enemy_units=[UnitStub(3,position=(24,10))]
    command=Action(Point2((24,10)),True)
    assert r.guard_action(unit,command,group_units=[unit,ally]) is command


@pytest.mark.parametrize("flags",[dict(is_snapshot=True),dict(is_memory=True),dict(is_visible=False),dict(position=(80,10))])
def test_remote_or_unseen_enemy_does_not_cancel_station(flags):
    r=rules();unit=UnitStub(1,position=(11,10),sight_range=11)
    options=dict(position=(20,10));options.update(flags)
    r.ai.all_enemy_units=[UnitStub(2,**options)]
    command=Action(Point2((11,11)),False)
    assert r.guard_action(unit,command).ability==A.HOLDPOSITION_HOLD


def test_station_resumes_when_visible_contact_is_gone():
    r=rules();unit=UnitStub(1,position=(11,10),sight_range=11)
    r.ai.all_enemy_units=[UnitStub(2,position=(20,10))]
    command=Action(Point2((11,11)),False)
    assert r.guard_action(unit,command) is command
    r.ai.all_enemy_units=[]
    assert r.guard_action(unit,command).ability==A.HOLDPOSITION_HOLD


@pytest.mark.parametrize("kind", [U.MARINE, U.STALKER, U.ROACH])
@pytest.mark.parametrize("keep_order", [False, True])
def test_defend_does_not_follow_a_visible_fleeing_enemy_far_from_the_objective(kind, keep_order):
    r = rules()
    r.boundary = lambda p: p.distance_to(r.return_point) <= 18
    unit = UnitStub(1, kind, position=(60, 10), sight_range=11)
    enemy = UnitStub(2, U.MARINE, position=(70, 10))
    r.ai.all_enemy_units = [enemy]
    chase = NoAction() if keep_order else Action(enemy, True)
    command = r.guard_action(unit, chase)
    assert not command.is_attack
    assert command.position == r.return_point


def test_defender_outside_its_zone_keeps_kiting_when_enemy_can_shoot_it():
    r = rules()
    r.boundary = lambda p: p.distance_to(r.return_point) <= 18
    unit = UnitStub(1, U.MARINE, position=(60, 10), sight_range=11)
    enemy = UnitStub(2, U.MARAUDER, position=(66, 10))
    r.ai.all_enemy_units = [enemy]
    r.unit_values = NS(real_range=lambda attacker, target: 7 if attacker is enemy else 5)
    kite = Action(Point2((57, 10)), False)
    assert r.guard_action(unit, kite) is kite


def test_sieged_tank_keeps_firing_with_shared_vision_beyond_its_own_sight():
    r=rules();r.unit_values=NS(real_range=lambda *_:14)
    tank=UnitStub(1,U.SIEGETANKSIEGED,position=(13,10),sight_range=11)
    r.ai.all_enemy_units=[UnitStub(2,position=(26,10))]
    command=NoAction()
    assert r.guard_action(tank,command) is command


def test_close_ground_combat_overrides_mobile_tank_station_deployment(monkeypatch):
    from sharpy.combat.terran import MicroTanks
    tank=UnitStub(1,U.SIEGETANK,position=(10,10))
    enemy=UnitStub(2,position=(13,10))
    micro=MicroDefendTanks();_configure_defend_micro(micro,Point2((10,10)))
    micro._local_ground_enemies=lambda _:NS(exists=True,closest_distance_to=lambda _:3)
    combat=Action(enemy,True)
    monkeypatch.setattr(MicroTanks,'unit_solve_combat',lambda *_:combat)
    assert micro.unit_solve_combat(tank,Action(Point2((10,10)),False)) is combat


@pytest.mark.parametrize("ability",[A.SIEGEMODE_SIEGEMODE,A.UNSIEGE_UNSIEGE])
def test_pending_tank_transform_is_not_reissued_or_interrupted(ability):
    tank=UnitStub(1,U.SIEGETANK,position=(10,10),orders=[NS(ability=NS(id=ability))])
    micro=MicroDefendTanks();_configure_defend_micro(micro,Point2((10,10)))
    assert isinstance(micro.unit_solve_combat(tank,Action(Point2((10,10)),False)),NoAction)
    r=rules();r.boundary=lambda _:False
    assert isinstance(r.guard_action(tank,Action(Point2((30,30)),False)),NoAction)


def test_engine_callback_failure_is_not_recorded_as_game_defeat(monkeypatch):
    from unittest.mock import AsyncMock
    from sc2bench_env.backends.sharpy.bot import BenchBot
    from sc2bench_env.backends.sharpy.backend import _Bridge
    bot=object.__new__(BenchBot);bot.bridge=_Bridge()
    monkeypatch.setattr(bot,"_run_platform_step",AsyncMock(side_effect=RuntimeError("controller failed")))
    with pytest.raises(RuntimeError,match="controller failed"):
        asyncio.run(bot.on_step(1))
    bot.bridge.on_game_end("Result.Defeat")
    assert bot.bridge.snapshot.end_reason=="backend_error"
    assert bot.bridge.snapshot.result.startswith("error:RuntimeError")


def test_station_is_not_reselected_when_its_tiles_become_occupied(monkeypatch):
    from sc2bench_env.backends.sharpy import holding
    first,second=Point2((10,10)),Point2((20,10))
    choices=[first,second]
    monkeypatch.setattr(holding,"ramp_slots",lambda *_:choices)
    ai=NS(structures=[]);zone=NS(center_location=Point2((0,0)))
    assert holding.stable_ramp_station(ai,zone,first)==first
    choices[:]=[second]
    assert holding.stable_ramp_station(ai,zone,first)==first
    ai.structures=[NS(position=first,radius=1.5)]
    assert holding.stable_ramp_station(ai,zone,first)==second
