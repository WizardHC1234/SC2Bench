"""Two gas requests must not dispatch two workers onto the same geyser."""
import json
import os
from pathlib import Path
from collections import Counter
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


@pytest.mark.parametrize('race,target',[('terran','refinery'),('protoss','assimilator'),('zerg','extractor')])
def test_two_gas_requests_choose_distinct_geysers(monkeypatch,race,target):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2bench_env.backends.sharpy.bot import BenchBot
    state=dict(commands=[],errors=[],funded=False)
    original=BenchBot.do
    def do(ai,action,*args,**kwargs):
        before=len(ai.actions)
        result=original(ai,action,*args,**kwargs)
        if len(ai.actions)>before and any(name in action.ability.name for name in ('BUILD_REFINERY','BUILD_ASSIMILATOR','BUILD_EXTRACTOR')):
            state['commands'].append(dict(time=ai.time,worker=action.unit.tag,
                geyser=action.target.tag,queue=action.queue,orders=[str(o.ability.id) for o in action.unit.orders]))
        return result
    monkeypatch.setattr(BenchBot,'do',do)
    async def collect(act):
        if not state['funded']:
            await act.ai.client.debug_minerals();state['funded']=True
        for e in act.ai.state.action_errors:
            state['errors'].append(dict(time=act.ai.time,ability=e.ability_id,result=e.result,worker=e.unit_tag))
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir=f'records/platform_audit_contract_20261031/parallel_gas_{race}')
    try:
        env.reset(EpisodeConfig(race=race,enemy_race='zerg',opponent='veryeasy',map_name='AcropolisLE',game_time_limit_seconds=100))
        drive(env,[{'name':'build','arguments':{'target':target}}, {'name':'build','arguments':{'target':target}},
                   {'name':'advance','arguments':{'seconds':5}}])
        for _ in range(10):drive(env,[{'name':'advance','arguments':{'seconds':3}}])
        snapshot=env.backend.snapshot()
        state['gas_structures']=dict(snapshot.buildings)
        state['under_construction']=dict(snapshot.info.get('under_construction') or {})
        state['same_frame_duplicates']=[dict(time=t,geyser=g,count=n) for (t,g),n in
            Counter((r['time'],r['geyser']) for r in state['commands']).items() if n>1]
        Path(f'analysis/platform_audit/parallel_gas_{race}_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert not state['same_frame_duplicates'],state
        assert not state['errors'],state
        assert snapshot.buildings.get(target,0)+state['under_construction'].get(target,0)==2,state
    finally:
        env.close()
