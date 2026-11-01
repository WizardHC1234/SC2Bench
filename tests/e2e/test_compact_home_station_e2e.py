"""Native passive home gathering across races and maps, without model orders."""
import json
import os
import math
import statistics
from pathlib import Path
import pytest
from sc2bench_env import Environment,ToolCall
from sc2bench_env.interface.config import EpisodeConfig
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


@pytest.mark.parametrize('race,map_name,kind',[
    ('terran','AcropolisLE','MARINE'),
    ('protoss','TritonLE','STALKER'),
    ('zerg','KairosJunctionLE','ROACH'),
])
def test_home_gather_is_compact_and_stable(monkeypatch,race,map_name,kind):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2bench_env.backends.sharpy.micro import MissionMicroRules
    legacy=os.environ.get('SC2BENCH_LEGACY_STATION')=='1'
    if legacy:
        guard=MissionMicroRules.guard_action
        def original_radius(rules,*args,**kwargs):
            compact=rules.compact_station;rules.compact_station=False
            try:return guard(rules,*args,**kwargs)
            finally:rules.compact_station=compact
        monkeypatch.setattr(MissionMicroRules,'guard_action',original_radius)
    state=dict(race=race,map=map_name,legacy=legacy,setup=False,errors=[],samples=[])
    async def collect(act):
        ai=act.ai;point=ai.bench_home_gather.home_point()
        state['errors'].extend(dict(seconds=ai.time,ability=e.ability_id,result=e.result)
                               for e in ai.state.action_errors)
        if not state['setup']:
            await ai.client.debug_create_unit([[getattr(U,kind),24,ai.start_location,ai.player_id]])
            state['setup']=True
            return
        if not state['samples'] or ai.time-state['samples'][-1]['seconds']>=3:
            units=list(ai.units(getattr(U,kind)))
            state['samples'].append(dict(seconds=ai.time,point=list(point),
                units={str(u.tag):list(u.position) for u in units},
                median_radius=statistics.median([u.distance_to(point) for u in units]) if units else None,
                inner_count=sum(u.distance_to(point)<2 for u in units)))
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir=f'records/platform_audit_contract_20261101/compact_home_{race}')
    try:
        env.reset(EpisodeConfig(race=race,enemy_race='terran',opponent='veryeasy',map_name=map_name,
                               seed=202611310,game_time_limit_seconds=100))
        for _ in range(15):
            turn=env.begin_tool_turn();turn.call(ToolCall('advance',{'seconds':3}))
            _,_,done,info=env.step(turn.finish());assert not done,info
        final=state['samples'][-1];earlier=state['samples'][-3]
        state['max_late_displacement']=max(math.dist(point,earlier['units'][tag])
            for tag,point in final['units'].items() if tag in earlier['units'])
        assert len(final['units'])==24,state
        assert final['inner_count']>=3,state
        assert final['median_radius']<4,state
        assert state['max_late_displacement']<1,state
        assert not state['errors'],state
    finally:
        state['record_directory']=str(env.record_path)
        suffix='before' if legacy else 'fixed'
        Path(f'analysis/platform_audit/compact_home_{race}_{suffix}.json').write_text(
            json.dumps(state,indent=2),encoding='utf8')
        env.close()
