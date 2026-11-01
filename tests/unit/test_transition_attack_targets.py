"""Native Viking transition flags cannot make Roaches shoot an air target."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from sc2.position import Point2
from sc2.ids.unit_typeid import UnitTypeId as U
from sharpy.combat import Action, NoAction
from sharpy.combat.group_combat_manager import GroupCombatManager
from sc2bench_env.backends.sharpy.bot import BenchBot


@pytest.mark.parametrize('kind',[U.MARINE,U.STALKER,U.HYDRALISK])
def test_interceptor_focus_uses_native_position_attack_without_changing_normal_targets(kind):
    combat=GroupCombatManager();combat.ai=NS(time=0)
    combat.unit_values=NS(can_shoot_air=lambda _:True)
    unit=NS(type_id=kind)
    target=NS(type_id=U.INTERCEPTOR,position=Point2((4,4)))
    result=combat.legal_attack_action(unit,Action(target,True),Point2((20,20)))
    assert result.is_attack and result.target==target.position and result.ability is None
    result=combat.legal_attack_action(unit,Action(target,True),Point2((20,20)),attack_move=False)
    assert not result.is_attack and result.target==Point2((20,20))
    target.type_id=U.CARRIER
    normal=Action(target,True)
    assert combat.legal_attack_action(unit,normal,Point2((20,20))) is normal


@pytest.mark.parametrize('kind,rejected,air,blocked',[
    (U.VIKINGFIGHTER,0,False,True),
    (U.VIKINGASSAULT,12,False,True),
    (U.VIKINGASSAULT,9,False,False),
    (U.VIKINGASSAULT,0,False,False),
    (U.VIKINGFIGHTER,12,True,False),
])
def test_ground_only_target_validation_keeps_legal_ground_and_air_shots(kind,rejected,air,blocked):
    combat=GroupCombatManager()
    combat.ai=NS(time=10,_bench_ground_attack_rejections={2:rejected})
    combat.unit_values=NS(can_shoot_air=lambda _:air)
    target=NS(tag=2,type_id=kind,is_flying=False,position=Point2((5,5)))
    unit=NS(type_id=U.ROACH,position=Point2((0,0)))
    action=Action(target,True)
    result=combat.legal_attack_action(unit,action,Point2((20,20)))
    if blocked:
        assert result.is_attack and result.target==Point2((20,20))
    else:
        assert result is action


def test_deployed_tank_does_not_receive_an_invalid_attack_move_for_an_air_target():
    combat=GroupCombatManager()
    combat.ai=NS(time=10)
    combat.unit_values=NS(can_shoot_air=lambda _:False)
    target=NS(tag=2,type_id=U.VIKINGFIGHTER)
    tank=NS(type_id=U.SIEGETANKSIEGED,position=Point2((0,0)))
    assert isinstance(combat.legal_attack_action(tank,Action(target,True),Point2((20,20))),NoAction)


def test_invalid_target_does_not_change_a_retreat_into_an_attack_move():
    combat=GroupCombatManager();combat.ai=NS(time=10)
    combat.unit_values=NS(can_shoot_air=lambda _:False)
    unit=NS(type_id=U.ROACH,position=Point2((0,0)))
    result=combat.legal_attack_action(unit,Action(NS(tag=2,type_id=U.VIKINGFIGHTER),True),
                                      Point2((20,20)),attack_move=False)
    assert not result.is_attack


def test_ground_weapon_also_rejects_an_ordinary_air_target():
    combat=GroupCombatManager();combat.ai=NS(time=10)
    combat.unit_values=NS(can_shoot_air=lambda _:False)
    unit=NS(type_id=U.ROACH,position=Point2((0,0)))
    target=NS(tag=2,type_id=U.MEDIVAC,is_flying=True)
    result=combat.legal_attack_action(unit,Action(target,True),Point2((20,20)))
    assert result.target==Point2((20,20))


@pytest.mark.parametrize('melee',[False,True])
@pytest.mark.parametrize('kind,until,expected',[(U.VIKINGFIGHTER,0,3),(U.VIKINGASSAULT,12,3),
                                               (U.VIKINGASSAULT,9,2)])
def test_focus_fire_selects_a_legal_ground_enemy_before_command_submission(melee,kind,until,expected):
    from sc2.data import Race
    from sc2.units import Units
    from sharpy.combat import MoveType
    from sharpy.combat.default_micro_methods import DefaultMicroMethods as Methods
    def enemy(tag,type_id,x):
        return NS(tag=tag,type_id=type_id,is_flying=False,position=Point2((x,0)),
                  shield_health_percentage=.5,health=100,is_structure=False)
    enemies=[enemy(2,kind,1),enemy(3,U.MARINE,2)]
    unit=NS(position=Point2((0,0)),is_structure=False,calculate_damage_vs_target=lambda _:(6,0,0))
    unit.distance_to=lambda other:unit.position.distance_to(getattr(other,'position',other))
    step=NS(ai=NS(time=10,_bench_ground_attack_rejections={2:until}),
        unit_values=NS(ground_range=lambda _:1,real_range=lambda *_:2,
                       can_shoot_air=lambda _:False,can_shoot_ground=lambda _:True),
        min_range=lambda _:6,cache=NS(enemy_in_range=lambda *_:Units(enemies,None)),
        last_targeted=lambda _:None,knowledge=NS(enemy_race=Race.Terran),
        move_type=MoveType.Assault,focus_fired={},is_target=lambda _:True)
    method=Methods.melee_focus_fire if melee else Methods.focus_fire
    result=method(step,unit,Action(Point2((20,20)),True),{kind:100,U.MARINE:1})
    assert result.target.tag==expected


def test_native_ground_target_rejection_marks_only_the_actual_target():
    from s2clientprotocol import sc2api_pb2 as api,raw_pb2 as raw
    bot=object.__new__(BenchBot)
    bot.state=NS(game_loop=224)
    command=raw.ActionRawUnitCommand(ability_id=3674,unit_tags=[11],target_unit_tag=22)
    request=api.RequestAction(actions=[api.Action(action_raw=raw.ActionRaw(unit_command=command))])
    bot._client=NS(_execute=AsyncMock(return_value=NS(action=NS(result=[113]))))
    bot._capture_action_results()
    asyncio.run(bot.client._execute(action=request))
    assert bot._bench_ground_attack_rejections=={22:11.5}
    assert bot._pending_submission_errors[0].result==113


@pytest.mark.parametrize('kind',[U.MARINE,U.STALKER,U.HYDRALISK,U.SCV])
def test_native_unsupported_tag_target_uses_position_for_only_that_type_and_target(kind):
    from sc2.units import Units
    from s2clientprotocol import sc2api_pb2 as api,raw_pb2 as raw
    bot=object.__new__(BenchBot);bot.state=NS(game_loop=224)
    actor=NS(tag=11,type_id=kind)
    bot.units=Units([actor],bot);bot.structures=Units([],bot)
    command=raw.ActionRawUnitCommand(ability_id=3674,unit_tags=[11],target_unit_tag=22)
    request=api.RequestAction(actions=[api.Action(action_raw=raw.ActionRaw(unit_command=command))])
    bot._client=NS(_execute=AsyncMock(return_value=NS(action=NS(result=[2]))))
    bot._capture_action_results();asyncio.run(bot.client._execute(action=request))
    assert bot._bench_unit_attack_rejections=={(kind.value,22)}
    combat=GroupCombatManager();combat.ai=bot
    combat.unit_values=NS(can_shoot_air=lambda _:True)
    target=NS(tag=22,type_id=U.LOCUSTMP,position=Point2((4,4)))
    result=combat.legal_attack_action(actor,Action(target,True),Point2((20,20)))
    assert result.is_attack and result.target==target.position
    retreat=combat.legal_attack_action(actor,Action(target,True),Point2((20,20)),attack_move=False)
    assert not retreat.is_attack and retreat.target==Point2((20,20))
    normal=Action(target,True)
    other=U.STALKER if kind!=U.STALKER else U.MARINE
    assert combat.legal_attack_action(NS(type_id=other),normal,Point2((20,20))) is normal
    target.tag=23
    assert combat.legal_attack_action(actor,normal,Point2((20,20))) is normal


@pytest.mark.parametrize('kind',[U.SIEGETANKSIEGED,U.LURKERMPBURROWED,U.PHOTONCANNON])
def test_deployed_unit_keeps_native_fire_after_its_tag_target_is_rejected(kind):
    combat=GroupCombatManager();combat.ai=NS(_bench_unit_attack_rejections={(kind.value,22)})
    tank=NS(type_id=kind,is_structure=kind==U.PHOTONCANNON)
    target=NS(tag=22,type_id=U.LOCUSTMP,position=Point2((4,4)))
    assert isinstance(combat.legal_attack_action(tank,Action(target,True),Point2((20,20))),NoAction)
