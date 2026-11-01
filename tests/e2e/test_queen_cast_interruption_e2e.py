"""Confirm a queued defense command and the resulting inject failure natively."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


def test_native_defense_interruption_is_reported(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2bench_env.backends.sharpy.bot import BenchBot
    state=dict(spawned=False,charged=False,enemy_spawned=False,commands=[],errors=[],frames=[])
    send=BenchBot.do
    def command(ai,action,*args,**kwargs):
        count=len(ai.actions)
        result=send(ai,action,*args,**kwargs)
        if len(ai.actions)>count and action.unit.type_id==U.QUEEN:
            casts=[getattr(t.get('_act'),'_pending_cast',None) for t in ai._macro_tasks]
            state['commands'].append(dict(time=ai.time,ability=action.ability.name,
                role=int(ai.roles.unit_role(action.unit)),
                marked=any(c and c.get('interrupted_by_combat') for c in casts)))
        return result
    monkeypatch.setattr(BenchBot,'do',command)
    async def collect(act):
        ai=act.ai
        state['errors'].extend(dict(time=ai.time,ability=e.ability_id,result=e.result) for e in ai.state.action_errors)
        if not state['spawned']:
            points=[ai.start_location.offset((x,y)) for x,y in [(12,0),(0,12),(-12,0),(0,-12)]]
            point=next(p for p in points if ai.in_pathing_grid(p)
                       and ai.get_terrain_height(p)==ai.get_terrain_height(ai.start_location))
            await ai.client.debug_create_unit([[U.QUEEN,1,point,ai.player_id]])
            state['spawned']=True
            return
        queens=ai.units(U.QUEEN)
        if not queens:return
        queen=queens.first
        if not state['charged']:
            await ai.client.debug_set_unit_value([queen.tag],1,100)
            state['charged']=True
            return
        pending=[getattr(t.get('_act'),'_pending_cast',None) for t in ai._macro_tasks]
        state['queen_position']=list(queen.position)
        state['hall_positions']=[list(h.position) for h in ai.townhalls]
        enemies=[e for e in ai.enemy_units if e.is_visible and not e.is_memory and not e.is_snapshot]
        state['frames'].append(dict(time=ai.time,position=list(queen.position),
            orders=[o.ability.id.name for o in queen.orders],enemies=len(enemies)))
        if any(pending) and enemies and not state.get('defense_submitted'):
            from sharpy.managers.core.roles import UnitTask
            ai.roles.set_task(UnitTask.Defending,queen)
            queen.attack(min(enemies,key=lambda e:queen.distance_to(e)))
            state['defense_submitted']=True
        if any(pending) and not state['enemy_spawned']:
            await ai.client.debug_create_unit([[U.STALKER,1,queen.position,3-ai.player_id]])
            state['enemy_spawned']=True
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir='records/platform_audit_contract_20261031/queen_local_combat')
    wait=lambda n:{'name':'advance','arguments':{'seconds':n}}
    try:
        env.reset(EpisodeConfig(race='zerg',enemy_race='protoss',opponent='veryeasy',map_name='TritonLE',
            seed=202610954,game_time_limit_seconds=60))
        drive(env,[wait(3)])
        drive(env,[{'name':'inject_larva','arguments':{}},wait(1)])
        for _ in range(12):drive(env,[wait(1)])
        state['history']=env.task_manager.production_priority_summary()
        state['demands']=[dict(action=d.action,state=d.state.value,reason=d.failure_reason)
                          for d in env.task_manager.demands.values()]
        state['failed']={key:d.failure_reason for key,d in env.task_manager.demands.items() if d.failure_reason}
        Path('analysis/platform_audit/queen_local_combat_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert state['enemy_spawned'] and any(c['marked'] for c in state['commands']),state
        assert any('cast_interrupted_by_combat' in str(v) for v in state['failed'].values()),state
        assert not state['errors'],state
    finally:
        env.close()
