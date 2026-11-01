"""Power loss and restoration through public training requests in the native client."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


def test_unpowered_production_reports_power_and_resumes_when_power_returns(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2bench_env.backends.sharpy.races.protoss_production import read_production_capacity
    from sc2bench_env.backends.sharpy.races.protoss import ProtossAdapter
    state={'spawned':False,'restore':False,'finish':False}
    async def collect(act):
        ai=act.ai
        if state['finish']:
            await ai.client.debug_kill_unit([b.tag for b in ai.structures]);state['finish']=False;return
        if not state['spawned']:
            point=ai.start_location.towards(ai.game_info.map_center,12)
            state['point']=list(point)
            await ai.client.debug_all_resources()
            await ai.client.debug_create_unit([[U.ROBOTICSFACILITY,1,point,ai.player_id],
                [U.CYBERNETICSCORE,1,point.offset((0,5)),ai.player_id]])
            state['spawned']=True;return
        if state['restore']:
            from sc2.position import Point2
            await ai.client.debug_create_unit([[U.PYLON,1,Point2(state['point']).offset((4,0)),ai.player_id]])
            state['restore']=False
        robos=ai.structures(U.ROBOTICSFACILITY).ready
        if robos:
            state['powered']=robos.first.is_powered
            state['capacity']=next(r for r in read_production_capacity(ai,ProtossAdapter()) if r['facility']=='robotics_facility')
        state['immortals']=len(ai.units(U.IMMORTAL))
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir='records/platform_audit_comprehensive_20261031/power_recovery')
    def wait(n=2):return {'name':'advance','arguments':{'seconds':n}}
    try:
        env.reset(EpisodeConfig(race='protoss',opponent='veryeasy',map_name='KairosJunctionLE',game_time_limit_seconds=120))
        drive(env,[wait(2)])
        obs,feedback,done,_=drive(env,[{'name':'train','arguments':{'target':'immortal','count':1}},wait(4)])
        assert feedback.receipts and feedback.receipts[0].result=='accepted',feedback
        state['unpowered_observation']=obs.training.get('immortal',{})
        state['unpowered_capacity']=dict(state['capacity'])
        assert not done and not state['powered'],state
        assert state['unpowered_observation'].get('waiting_for')=='unpowered',state
        assert state['capacity']['free_slots']==0 and state['capacity']['unpowered']==1,state
        state['restore']=True
        for _ in range(35):
            drive(env,[wait()])
            if state.get('immortals')==1:break
        assert state['powered'] and state['immortals']==1,state
        assert not any(d.target=='immortal' for d in env.task_manager.active_demands()),state
        Path('analysis/platform_audit/power_recovery_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        state['finish']=True;drive(env,[wait()])
    finally:
        env.close()
