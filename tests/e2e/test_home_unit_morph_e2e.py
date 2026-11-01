"""Public morph requests must work after source units have settled at home."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment, ToolCall
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive, query
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


@pytest.mark.parametrize('race,source,target,technology,overcap',[
    ('zerg','ZERGLING','baneling',['SPAWNINGPOOL','BANELINGNEST'],False),
    ('zerg','ROACH','ravager',['SPAWNINGPOOL','ROACHWARREN'],False),
    ('zerg','HYDRALISK','lurker',['HIVE','HYDRALISKDEN','LURKERDENMP'],False),
    ('zerg','CORRUPTOR','brood_lord',['HIVE','GREATERSPIRE'],False),
    ('zerg','ZERGLING','baneling',['SPAWNINGPOOL','BANELINGNEST'],True),
    ('protoss','HIGHTEMPLAR','archon',[],False)])
def test_morph_a_stationed_home_unit(monkeypatch,race,source,target,technology,overcap):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2.ids.ability_id import AbilityId as A
    state=dict(spawned=False,hold_seen=False,samples=[],errors=[],abilities=[])
    async def collect(act):
        ai=act.ai
        state['errors'].extend(dict(time=ai.time,ability=e.ability_id,result=e.result) for e in ai.state.action_errors)
        if not state['spawned']:
            point=ai.bench_home_gather.home_point()
            buildings=[[getattr(U,name),1,ai.start_location.offset(((5,0),(-5,0),(0,5),(0,-5))[i]),ai.player_id]
                       for i,name in enumerate(technology)]
            count=2 if target=='archon' else 1
            rows=buildings+[[getattr(U,source),count,point,ai.player_id]]
            if overcap:rows.append([U.DRONE,4,ai.start_location,ai.player_id])
            await ai.client.debug_create_unit(rows)
            await ai.client.debug_all_resources()
            state['spawned']=True
            return
        for unit in ai.units(getattr(U,source)):
            orders=[o.ability.id for o in unit.orders]
            if not state['abilities']:
                state['abilities']=[a.value for a in await ai.get_available_abilities(unit)]
            if any(a in {A.HOLDPOSITION_HOLD,A.HOLDPOSITION} for a in orders):state['hold_seen']=True
            state['samples'].append(dict(time=ai.time,position=list(unit.position),orders=[a.name for a in orders],
                home=unit.tag in ai.bench_group0_tags))
    _install(monkeypatch,collect)
    label=target+'_overcap' if overcap else target
    env=Environment('sharpy',record_dir=f'records/platform_audit_contract_20261031/home_morph_{label}')
    try:
        env.reset(EpisodeConfig(race=race,enemy_race='terran',opponent='veryeasy',map_name='AcropolisLE',game_time_limit_seconds=100))
        observation,_,_,_=drive(env,[{'name':'advance','arguments':{'seconds':6}}])
        snapshot=env.backend.snapshot()
        state['baseline']=dict(minerals=snapshot.minerals,vespene=snapshot.vespene,buildings=dict(snapshot.buildings),
                               supply_used=snapshot.supply_used,supply_cap=snapshot.supply_cap)
        state['query_before']=query(env,ToolCall('query_production_state',{'targets':[target]}))
        assert state['hold_seen'],state
        if race=='zerg':
            row=next(r for r in state['query_before']['production_capacity'] if r['facility']==target+'_source')
            assert row['source_available']==1,state
        if overcap:assert snapshot.supply_used>snapshot.supply_cap,state
        drive(env,[{'name':'train','arguments':{'target':target,'count':1}},{'name':'advance','arguments':{'seconds':5}}])
        for _ in range(10):
            if env.backend.snapshot().units.get(target,0):break
            drive(env,[{'name':'advance','arguments':{'seconds':3}}])
        state['units']=dict(env.backend.snapshot().units)
        state['priority']=env.task_manager.production_priority_summary()
        Path(f'analysis/platform_audit/home_morph_{label}_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert state['units'].get(target,0)==1,state
        assert not state['errors'],state
    finally:
        env.close()
