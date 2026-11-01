"""Native rejection cases found while inspecting complete game footage."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock
import pytest
from sc2.ids.ability_id import AbilityId as A
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.position import Point2
from sc2.units import Units
from sharpy.combat import Action, NoAction, MoveType
from sharpy.combat.terran import MicroTanks
from sharpy.plans.tactics.terran import LowerDepots
from tests.unit.test_combat_execution import UnitStub, mission
from tests.unit.test_platform_audit_repairs import unit,inventory
from sc2bench_env.backends.sharpy.races import protoss_warp as warp


def test_unfinished_depot_is_not_told_to_morph():
    depot=MagicMock(type_id=U.SUPPLYDEPOT,is_ready=False,orders=[])
    act=LowerDepots();act.ai=NS(enemy_units=Units([],None))
    act.cache=NS(own=lambda kind:Units([depot] if kind==U.SUPPLYDEPOT else [],None))
    assert asyncio.run(act.execute())
    depot.assert_not_called()


def test_sieged_tank_does_not_receive_an_invalid_attack_move():
    tank=UnitStub(1,U.SIEGETANKSIEGED)
    micro=MicroTanks();micro.move_type=MoveType.Assault;micro.ai=NS(time=0)
    micro._local_ground_enemies=lambda _:NS(exists=True,closest_distance_to=lambda _:10)
    assert isinstance(micro.unit_solve_combat(tank,Action(Point2((10,0)),True)),NoAction)
    assert isinstance(micro.unit_solve_combat(tank,Action(Point2((10,0)),False)),NoAction)


@pytest.mark.parametrize('charged,placement',[(False,None),(True,None),(True,Point2((12,12)))])
def test_warp_needs_actual_charge_and_verified_placement(monkeypatch,charged,placement):
    gate=unit(1,U.WARPGATE);ai=inventory(structures=[gate])
    ai.already_pending=lambda _:0;ai.can_afford=lambda _:True
    ai._game_data=NS(units={U.STALKER.value:NS(creation_ability=NS(id=A.GATEWAYTRAIN_STALKER))})
    ai.find_placement=AsyncMock(return_value=placement)
    monkeypatch.setattr(warp,'_ready_warpgates',AsyncMock(return_value=[gate]))
    monkeypatch.setattr(warp,'home_warp_point',lambda _:Point2((10,10)))
    monkeypatch.setattr(warp,'_raw_ability_ids',AsyncMock(return_value={1:{A.WARPGATETRAIN_STALKER.value} if charged else set()}))
    act=warp.ActGatewayTrain(U.STALKER,1);act.ai=ai
    asyncio.run(act.execute())
    if not charged:
        gate.warp_in.assert_not_called();ai.find_placement.assert_not_awaited()
        assert act.waiting_reason=='warpgate_cooldown'
    elif placement is None:
        gate.warp_in.assert_not_called();assert act.waiting_reason=='no_powered_warp_location'
    else:
        gate.warp_in.assert_called_once_with(U.STALKER,placement)


@pytest.mark.parametrize('own_kind',[U.MARINE,U.STALKER,U.ROACH])
def test_attack_goal_uses_zone_center_and_never_calls_a_defense_station(own_kind):
    act=mission(own=[UnitStub(1,own_kind)])
    center=Point2((33.5,138.5))
    zone=NS(center_location=center,gather_point=Point2((42,124)),our_townhall=None)
    act.ai.zone_registry=NS(resolve_zone=lambda *_:zone)
    act.zone_manager=NS()
    act._defend_station=lambda *_:pytest.fail('attack must not select a defense station')
    assert act._resolve_target()==center


def test_immediate_native_rejections_are_preserved_and_deduplicated(monkeypatch):
    from sc2bench_env.backends.sharpy.bot import BenchBot
    from sharpy.knowledges import KnowledgeBot
    from s2clientprotocol import sc2api_pb2 as api,raw_pb2 as raw
    bot=object.__new__(BenchBot)
    request=api.RequestAction(actions=[api.Action(action_raw=raw.ActionRaw(
        unit_command=raw.ActionRawUnitCommand(ability_id=16,unit_tags=[11,12])))])
    response=NS(action=NS(result=[2]))
    bot._client=NS(_execute=AsyncMock(return_value=response))
    bot._capture_action_results()
    assert asyncio.run(bot.client._execute(action=request)) is response
    assert [e.unit_tag for e in bot._pending_submission_errors]==[11,12]
    state=NS(action_errors=[NS(unit_tag=11,ability_id=16,result=2)])
    monkeypatch.setattr(KnowledgeBot,'_prepare_step',lambda self,state,_:setattr(self,'state',state))
    bot._prepare_step(state,None)
    assert [e.unit_tag for e in state.action_errors]==[11,12]
    assert not bot._pending_submission_errors


@pytest.mark.parametrize('hall',[U.NEXUS,U.COMMANDCENTER,U.HATCHERY])
@pytest.mark.parametrize('has_minerals',[True,False])
def test_offset_expansion_never_becomes_a_gather_target(hall,has_minerals):
    from sharpy.plans.tactics.distribute_workers import DistributeWorkers
    townhall=NS(type_id=hall,position=Point2((12,10)))
    mineral=NS(type_id=U.MINERALFIELD,tag=99)
    zone=NS(center_location=Point2((10,10)),check_best_mineral_field=lambda:mineral if has_minerals else None)
    act=DistributeWorkers();act.zone_manager=NS(zone_for_unit=lambda _:zone,expansion_zones=[zone])
    act.get_new_work=lambda *_:townhall;act.print=lambda *_:None
    worker=NS(tag=1);calls=[]
    act.assign_to_work=lambda worker,target:calls.append(target)
    assert asyncio.run(act.set_work(worker))
    assert calls==([mineral] if has_minerals else [])


@pytest.mark.parametrize('blocked_by_unit',[True,False])
def test_warp_placement_avoids_units_and_same_frame_warps(blocked_by_unit):
    point=Point2((10,10));ability=A.WARPGATETRAIN_STALKER
    ai=NS(state=NS(game_loop=10),units=[NS(position=point,radius=.5,is_flying=False)] if blocked_by_unit else [],
          _game_data=NS(abilities={ability.value:object()}),
          find_placement=AsyncMock(return_value=point),can_place=AsyncMock(side_effect=lambda _,ps:[True]*len(ps)))
    ai._bench_warp_positions=(10,[] if blocked_by_unit else [point])
    result=asyncio.run(warp.clear_warp_placement(ai,ability,point))
    assert result.distance_to(point)>=1.5


def test_queen_does_not_cast_transfusion_on_herself(monkeypatch):
    from sharpy.combat.zerg.micro_queens import MicroQueens
    from sharpy.combat import GenericMicro
    queen=NS(tag=1,health=50,health_max=175,position=Point2((0,0)),has_buff=lambda _:False)
    micro=MicroQueens();micro.cd_manager=NS(is_ready=lambda *_:True)
    micro.cache=NS(own_in_range=lambda *_:[queen])
    command=NoAction()
    monkeypatch.setattr(GenericMicro,'unit_solve_combat',lambda *_:command)
    assert micro.unit_solve_combat(queen,command) is command


@pytest.mark.parametrize('ready',[False,True])
def test_queen_transfusion_requires_a_completed_target(monkeypatch,ready):
    from sharpy.combat.zerg.micro_queens import MicroQueens
    from sharpy.combat import GenericMicro
    queen=NS(tag=1,position=Point2((0,0)))
    target=NS(tag=2,is_ready=ready,health=100,health_max=1500,has_buff=lambda _:False)
    micro=MicroQueens();micro.cd_manager=NS(is_ready=lambda *_:True)
    micro.cache=NS(own_in_range=lambda *_:[target])
    command=NoAction()
    monkeypatch.setattr(GenericMicro,'unit_solve_combat',lambda *_:command)
    result=micro.unit_solve_combat(queen,command)
    if ready:assert result.ability==A.TRANSFUSION_TRANSFUSION and result.target is target
    else:assert result is command
