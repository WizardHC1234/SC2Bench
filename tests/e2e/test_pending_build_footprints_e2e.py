"""Two native builders must not be sent to overlapping unfinished sites."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


@pytest.mark.parametrize('race,worker,first,second,technology',[
    ('terran','SCV','BARRACKS','ENGINEERINGBAY',['SUPPLYDEPOT']),
    ('protoss','PROBE','GATEWAY','ROBOTICSFACILITY',['PYLON','CYBERNETICSCORE']),
    ('zerg','DRONE','EVOLUTIONCHAMBER','ROACHWARREN',['SPAWNINGPOOL'])])
def test_two_builders_use_distinct_native_sites(monkeypatch,race,worker,first,second,technology):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sharpy.plans.acts import GridBuilding
    from sharpy.managers.core.roles import UnitTask
    from sc2bench_env.backends.sharpy.bot import BenchBot
    state=dict(phase=0,commands=[],errors=[],counts={},race=race)
    send=BenchBot.do
    def command(ai,action,*args,**kwargs):
        before=len(ai.actions)
        result=send(ai,action,*args,**kwargs)
        if (len(ai.actions)>before and action.unit.type_id in {U.SCV,U.PROBE,U.DRONE}
                and hasattr(action.target,'x') and 'BUILD' in action.ability.name):
            state['commands'].append(dict(time=ai.time,worker=action.unit.tag,
                ability=action.ability.name,point=list(action.target)))
        return result
    monkeypatch.setattr(BenchBot,'do',command)
    async def collect(act):
        ai=act.ai
        state['errors'].extend(dict(time=ai.time,ability=e.ability_id,result=e.result) for e in ai.state.action_errors)
        state['counts']={name:ai.structures(getattr(U,name)).ready.amount for name in (first,second)}
        if state['phase']==0:
            await ai.client.debug_all_resources()
            await ai.client.debug_kill_unit([u.tag for u in ai.workers])
            rows=[[getattr(U,name),1,ai.start_location.offset(offset),ai.player_id]
                  for name,offset in zip(technology,[(6,4),(-5,0)])]
            rows += [[getattr(U,worker),1,ai.start_location.offset((5,-4)),ai.player_id],
                     [getattr(U,worker),1,ai.start_location.offset((7,-4)),ai.player_id]]
            await ai.client.debug_create_unit(rows)
            state['phase']=1
        elif state['phase']==1 and len(ai.workers)>=2:
            point=await ai.find_placement(getattr(U,first),near=ai.start_location.offset((6,0)),
                max_distance=8,random_alternative=False)
            assert point is not None
            assert (await ai.can_place(getattr(U,second),[point]))[0]
            state['original_point']=list(point)
            method='build_'+race
            for name,builder in zip((first,second),list(ai.workers)[:2]):
                ai.roles.set_task(UnitTask.Building,builder)
                building=GridBuilding(getattr(U,name),1)
                await building.start(act.knowledge)
                await getattr(building,method)(builder,0,point)
            state['phase']=2
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir=f'records/platform_audit_contract_20261101/pending_sites_{race}')
    wait=lambda n:{'name':'advance','arguments':{'seconds':n}}
    try:
        env.reset(EpisodeConfig(race=race,enemy_race='terran',opponent='veryeasy',
            map_name='AcropolisLE',game_time_limit_seconds=100))
        for _ in range(15):
            drive(env,[wait(5)])
            if all(state['counts'].get(n,0)>=1 for n in (first,second)):break
        Path(f'analysis/platform_audit/pending_sites_{race}_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert len(state['commands'])==2,state
        assert state['commands'][0]['point']!=state['commands'][1]['point'],state
        assert all(state['counts'].get(n,0)>=1 for n in (first,second)),state
        assert not state['errors'],state
    finally:
        env.close()
