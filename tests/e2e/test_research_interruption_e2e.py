"""A lost native research queue must allow an explicit research retry."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.tools import ToolCall
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


@pytest.mark.parametrize('race,facility,kind,research',[
    ('terran','engineering_bay','ENGINEERINGBAY','infantry_weapons_1'),
    ('protoss','forge','FORGE','ground_weapons_1'),
    ('zerg','evolution_chamber','EVOLUTIONCHAMBER','melee_attacks_1')])
def test_destroyed_research_facility_allows_retry(monkeypatch,race,facility,kind,research):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2.position import Point2
    state={'race':race,'phase':0,'kill':False,'restore':False,'finish':False}
    async def collect(act):
        ai=act.ai
        if state['finish']:
            await ai.client.debug_kill_unit([b.tag for b in ai.structures]);state['finish']=False;return
        if not state['phase']:
            point=ai.start_location.towards(ai.game_info.map_center,7)
            state['point']=list(point)
            await ai.client.debug_all_resources()
            rows=[[getattr(U,kind),1,point,ai.player_id]]
            if race=='protoss':rows.append([U.PYLON,1,point.offset((4,0)),ai.player_id])
            await ai.client.debug_create_unit(rows);state['phase']=1
        if state['kill']:
            await ai.client.debug_kill_unit([b.tag for b in ai.structures(getattr(U,kind))])
            state['kill']=False
        if state['restore']:
            await ai.client.debug_create_unit([[getattr(U,kind),1,Point2(state['point']),ai.player_id]])
            state['restore']=False
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir=f'records/platform_audit_contract_20261031/research_loss_{race}')
    def wait(n=2):return {'name':'advance','arguments':{'seconds':n}}
    def save():
        Path(f'analysis/platform_audit/research_loss_{race}_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
    try:
        env.reset(EpisodeConfig(race=race,opponent='veryeasy',map_name='KairosJunctionLE',game_time_limit_seconds=400))
        drive(env,[wait(2)])
        obs,feedback,_,_=drive(env,[{'name':'research','arguments':{'target':research}},wait(3)])
        assert feedback.receipts[0].result=='accepted',feedback
        for _ in range(6):
            if obs.research.get(research)=='in_progress':break
            obs,_,_,_=drive(env,[wait()])
        assert obs.research.get(research)=='in_progress',obs.research
        state['queued']=obs.research[research]
        state['kill']=True
        obs,_,_,_=drive(env,[wait(4)])
        assert obs.research.get(research)=='interrupted' and obs.building.get(facility,{}).get('completed',0)==0,obs.research
        state['after_loss']=dict(obs.research)
        turn=env.begin_tool_turn()
        result=turn.call(ToolCall('research',{'target':research}))
        state['retry_status']=result.status;state['retry_result']=result.data;save()
        assert result.status=='staged',state
        state['restore']=True
        turn.call(ToolCall('advance',{'seconds':3}));env.step(turn.finish())
        for _ in range(75):
            obs,_,_,_=drive(env,[wait()])
            if obs.research.get(research)=='completed':break
        state['final_status']=obs.research.get(research);save()
        assert state['final_status']=='completed',state
        state['finish']=True;drive(env,[wait()])
    finally:
        env.close()
