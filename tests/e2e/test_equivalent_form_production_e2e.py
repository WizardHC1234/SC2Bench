"""Additional production must continue after existing assets change form."""
import json
import os
from pathlib import Path

import pytest

from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.tools import ToolCall
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


@pytest.mark.parametrize('race',['protoss','zerg'])
def test_additional_production_with_existing_upgraded_forms(monkeypatch,race):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    state=dict(race=race,setup=False,errors=[],frames=[])
    async def collect(act):
        ai=act.ai
        state['errors'].extend(dict(seconds=ai.time,actor=e.unit_tag,ability=e.ability_id,result=e.result)
                               for e in ai.state.action_errors)
        if not state['setup']:
            await ai.client.debug_all_resources()
            await ai.client.debug_fast_build()
            if race=='protoss':
                rows=[[U.WARPGATE,1,ai.start_location.offset((7,7)),ai.player_id],
                      [U.WARPGATE,1,ai.start_location.offset((-7,7)),ai.player_id]]
            else:
                rows=[[U.LAIR,1,ai.start_location.offset((7,0)),ai.player_id],
                      [U.SPAWNINGPOOL,1,ai.start_location.offset((-7,0)),ai.player_id],
                      [U.GREATERSPIRE,1,ai.start_location.offset((0,7)),ai.player_id],
                      [U.OVERLORDTRANSPORT,1,ai.start_location,ai.player_id]]
            await ai.client.debug_create_unit(rows)
            state['setup']=True
        state['frames'].append(dict(seconds=ai.time,tasks=[dict(target=t.get('target'),
            goal=t.get('to_count'),reason=getattr(t.get('_act'),'waiting_reason',None))
            for t in getattr(ai,'_macro_tasks',())]))
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir=f'records/platform_audit_contract_20261101/equivalent_form_{race}')
    def step(calls=(),seconds=3):
        turn=env.begin_tool_turn()
        for name,args in [*calls,('advance',{'seconds':seconds})]:
            result=turn.call(ToolCall(name,args))
            assert result.status!='rejected',result
        obs,feedback,done,info=env.step(turn.finish())
        assert not done,info
        return obs,feedback
    try:
        env.reset(EpisodeConfig(race=race,opponent='veryeasy',map_name='AcropolisLE',
                               game_time_limit_seconds=150))
        step()
        if race=='protoss':
            step([('build',{'target':'pylon'})])
            for _ in range(10):
                if env.backend.snapshot().buildings.get('pylon',0):break
                step()
            assert env.backend.snapshot().buildings.get('pylon',0)==1
        before=env.backend.snapshot()
        state['before']=dict(units=before.units,buildings=before.buildings)
        calls=([('build',{'target':'gateway'}),('build',{'target':'gateway'})]
               if race=='protoss' else [('build',{'target':'spire'}),('train',{'target':'overlord','count':2})])
        step(calls)
        for _ in range(12):
            current=env.backend.snapshot()
            ready=(current.buildings.get('gateway',0)==2 if race=='protoss' else
                   current.buildings.get('spire',0)==1
                   and current.units.get('overlord',0)==before.units.get('overlord',0)+2)
            # Build tasks finish at construction start; readiness comes later.
            if ready and not env.task_manager.production_priority_summary():break
            step()
        after=env.backend.snapshot()
        state['after']=dict(units=after.units,buildings=after.buildings)
        state['remaining']=env.task_manager.production_priority_summary()
        if race=='protoss':
            assert after.buildings.get('gateway',0)==2,state
            assert after.info.get('warpgate_completed',0)==2,state
        else:
            assert after.buildings.get('spire',0)==1,state
            assert after.buildings.get('greater_spire',0)==1,state
            assert after.units.get('overlord',0)==before.units.get('overlord',0)+2,state
            assert after.units.get('transport_overlord',0)==1,state
        assert not state['remaining'],state
        assert not state['errors'],state
    finally:
        state['record_directory']=str(env.record_path)
        Path(f'analysis/platform_audit/equivalent_form_{race}_native.json').write_text(
            json.dumps(state,indent=2),encoding='utf8')
        env.close()
