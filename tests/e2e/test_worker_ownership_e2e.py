"""Public scout replacement and unstarted cancellation with live workers."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


@pytest.mark.parametrize('race,target,kind',[
    ('terran','supply_depot','SUPPLYDEPOT'),('protoss','pylon','PYLON'),('zerg','spawning_pool','SPAWNINGPOOL')])
def test_scout_and_builder_are_independent_and_cancel_keeps_started_work(monkeypatch,race,target,kind):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sharpy.managers.core.roles import UnitTask
    state={'race':race,'funded':False,'finish':False,'role_checks':0,'wrong_scout_role':0,'shared_builder':0}
    async def collect(act):
        ai=act.ai
        if state['finish']:
            if ai.structures:await ai.client.debug_kill_unit([b.tag for b in ai.structures])
            state['finish']=False;return
        if not state['funded']:
            await ai.client.debug_all_resources();state['funded']=True
        for task in getattr(ai,'_macro_tasks',()):
            native=task.get('_act')
            if task.get('action')=='scout' and native is not None and not native._done:
                tag=native._scout_tag
                worker=ai.workers.find_by_tag(tag) if tag is not None else None
                if worker is not None:
                    state['scout_tag']=tag;state['role_checks']+=1
                    state['wrong_scout_role']+=act.roles.unit_role(worker)!=UnitTask.Scouting
                    for build in getattr(ai,'_macro_tasks',()):
                        if build.get('action')=='build':
                            state['shared_builder']+=getattr(build.get('_act'),'builder_tag',None)==tag
        kinds={getattr(U,kind)}
        if kind=='SUPPLYDEPOT':
            kinds.add(U.SUPPLYDEPOTLOWERED)
        structures=ai.structures.of_type(kinds)
        state['built']=len(structures)
        state['ready']=len(structures.ready)
        old=ai.workers.find_by_tag(state.get('first_scout'))
        state['old_scout_role']=UnitTask(act.roles.unit_role(old)).name if old is not None else None
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir=Path('records/platform_audit_comprehensive_20261031')/f'worker_ownership_{race}')
    def wait(seconds):return {'name':'advance','arguments':{'seconds':seconds}}
    try:
        env.reset(EpisodeConfig(race=race,enemy_race='terran',opponent='veryeasy',
            map_name='KairosJunctionLE',game_time_limit_seconds=120))
        drive(env,[wait(2)])
        obs,feedback,done,_=drive(env,[
            {'name':'scout','arguments':{'route':'all'}},
            {'name':'build','arguments':{'target':target}},
            {'name':'build','arguments':{'target':target}},
            {'name':'cancel','arguments':{'target_action':'build','target':target,'count':1}},wait(8)])
        assert not done and state['built']==1 and state['role_checks']>0,state
        assert state['wrong_scout_role']==0 and state['shared_builder']==0,state
        state['first_scout']=state['scout_tag']
        main=next(z['zone_id'] for z in obs.zone_state if z['zone_role']=='own_main')
        drive(env,[{'name':'cancel','arguments':{'target_action':'build','target':target}},
                   {'name':'scout','arguments':{'route':[main]}},wait(4)])
        for _ in range(12):
            if state['ready']==1:break
            drive(env,[wait(4)])
        drive(env,[wait(4)])
        assert state['built']==state['ready']==1,state
        assert state['old_scout_role'] in {'Idle','Gathering'},state
        state['active_scouts']=sum(d.action=='scout' for d in env.task_manager.active_demands())
        assert state['active_scouts']==0,state
        Path(f'analysis/platform_audit/worker_ownership_{race}_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        state['finish']=True;drive(env,[wait(2)])
    finally:
        env.close()
