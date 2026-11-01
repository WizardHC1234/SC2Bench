"""Scripted public production, combat and movement checks for Lurkers."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


def test_scripted_lurker_production_combat_and_movement(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    state=dict(spawned=False,enemy_spawned=False,engage=False,errors=[],raw=[],observations=[])
    async def collect(act):
        ai=act.ai
        state['errors'].extend(dict(seconds=ai.time,actor=e.unit_tag,ability=e.ability_id,result=e.result)
                               for e in ai.state.action_errors)
        if not state['spawned']:
            await ai.client.debug_all_resources()
            await ai.client.debug_create_unit([
                [U.HIVE,1,ai.start_location.offset((5,0)),ai.player_id],
                [U.HYDRALISKDEN,1,ai.start_location.offset((-5,0)),ai.player_id],
                [U.LURKERDENMP,1,ai.start_location.offset((0,5)),ai.player_id],
                [U.OVERLORD,2,ai.start_location,ai.player_id],
                [U.HYDRALISK,2,ai.bench_home_gather.home_point(),ai.player_id]])
            state['spawned']=True
            return
        state['raw'].append(dict(seconds=ai.time,loop=ai.state.game_loop,
            units=[dict(tag=u.tag,type=u.type_id.name,ready=u.is_ready,progress=u.build_progress,
                        orders=[o.ability.id.name for o in u.orders])
                   for u in ai.units if u.type_id in {U.HYDRALISK,U.LURKERMPEGG,U.LURKERMP,U.LURKERMPBURROWED}]))
        lurkers=ai.units({U.LURKERMP,U.LURKERMPBURROWED})
        if state['engage'] and lurkers and not state['enemy_spawned']:
            point=lurkers.first.position.towards(ai.start_location,6)
            await ai.client.debug_create_unit([[U.ROACH,6,point,3-ai.player_id]])
            state['enemy_spawned']=True
        enemies=[e for e in ai.enemy_units if e.type_id==U.ROACH and not e.is_memory]
        if enemies:
            state['enemy_min_health']=min(state.get('enemy_min_health',145),min(e.health for e in enemies))
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir='records/scripted_roster_20261101/lurker_contract')
    def step(calls=None,seconds=3):
        obs,feedback,done,info=drive(env,(calls or [])+[{'name':'advance','arguments':{'seconds':seconds}}])
        snap=env.backend.snapshot()
        state['observations'].append(dict(seconds=snap.game_time_seconds,units=dict(snap.units),
            pending=snap.info.get('in_production_units'),feedback=feedback.to_dict()))
        return obs,feedback,done,info
    try:
        env.reset(EpisodeConfig(race='zerg',enemy_race='terran',opponent='veryeasy',map_name='AcropolisLE',
                               game_time_limit_seconds=150))
        step(seconds=6)
        step([{'name':'train','arguments':{'target':'lurker','count':2}}])
        for _ in range(12):
            if env.backend.snapshot().units.get('lurker',0)>=2:break
            step()
        assert env.backend.snapshot().units.get('lurker',0)==2,state
        step([{'name':'combat','arguments':{'style':'defend','target':'zone_0','units':{'lurker':2}}}])
        step([{'name':'train','arguments':{'target':'hydralisk','count':4}}])
        for _ in range(12):
            if env.backend.snapshot().units.get('hydralisk',0)>=4:break
            step()
        assert env.backend.snapshot().units.get('hydralisk',0)==4,state
        assert any(sum(u['type']=='LURKERMPBURROWED' for u in row['units'])==2 for row in state['raw']),state
        step([{'name':'train','arguments':{'target':'lurker','count':2}}])
        for _ in range(12):
            if env.backend.snapshot().units.get('lurker',0)>=4:break
            step()
        assert env.backend.snapshot().units.get('lurker',0)==4,state
        assert env.backend.snapshot().units.get('hydralisk',0)==2,state
        state['engage']=True
        for _ in range(8):step(seconds=2)
        group=next(d.group for d in env.task_manager.demands.values() if d.action=='combat')
        step([{'name':'attack_group','arguments':{'group':group,'target':'zone_1'}}])
        for _ in range(8):step(seconds=2)
        Path('analysis/platform_audit/scripted_lurker_contract_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert state.get('enemy_min_health',145)<145,state
        assert not state['errors'],state['errors']
    finally:
        env.close()
