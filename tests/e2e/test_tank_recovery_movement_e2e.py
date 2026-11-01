"""Fault-injection check of deployment recovery in the real engine.

The rejected-event input is synthetic; movement and the final siege are native.
This is not a reproduction of the original collision or a model game.
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install, _spot

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


def test_recovery_moves_then_deploys_in_native_engine(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2.ids.ability_id import AbilityId as A
    from sharpy.combat import Action, NoAction
    from sharpy.combat.group_combat_manager import GroupCombatManager
    from sharpy.managers.core.roles import UnitTask
    state=dict(synthetic_error=True,spawned=False,injected=False,samples=[],commands=[])
    async def collect(act):
        ai=act.ai
        if not state['spawned']:
            point=_spot(act,ai.start_location,9)
            await ai.client.debug_create_unit([[U.SIEGETANK,1,point,ai.player_id]])
            state['spawned']=True
            return
        tanks=ai.units({U.SIEGETANK,U.SIEGETANKSIEGED})
        if not tanks:return
        tank=tanks.first
        act.roles.set_task(UnitTask.Reserved,tank)
        if not state['injected']:
            state['initial']=list(tank.position)
        combat=GroupCombatManager();combat.ai=ai;combat.rules=NS(boundary=lambda _:True)
        original=ai.state.action_errors
        if not state['injected']:
            ai.state.action_errors=[NS(unit_tag=tank.tag,ability_id=388,result=208)]
            state['injected']=True
        try:
            action=combat.legal_deployment_action(tank,
                Action(None,False,A.SIEGEMODE_SIEGEMODE) if tank.type_id==U.SIEGETANK else NoAction(),
                ai.start_location)
        finally:
            ai.state.action_errors=original
        state['commands'].append(dict(time=ai.time,ability=action.ability.name if action.ability else None,
            point=list(action.position) if action.position is not None else None))
        action.to_commmand(tank)
        state['samples'].append(dict(time=ai.time,tag=tank.tag,type=tank.type_id.name,position=list(tank.position)))
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir='records/platform_audit_contract_20261031/tank_recovery_fault_injection')
    try:
        env.reset(EpisodeConfig(race='terran',enemy_race='zerg',opponent='veryeasy',map_name='WorldofSleepersLE',game_time_limit_seconds=60))
        for _ in range(12):drive(env,[{'name':'advance','arguments':{'seconds':1}}])
        Path('analysis/platform_audit/tank_recovery_fault_injection_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert state['commands'][0]['point'] is not None,state
        assert any(r['type']=='SIEGETANKSIEGED' for r in state['samples']),state
        from sc2.position import Point2
        assert any(Point2(r['position']).distance_to(Point2(state['initial']))>.5 for r in state['samples']),state
    finally:
        env.close()
