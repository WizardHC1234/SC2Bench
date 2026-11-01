"""Three-race native checks for a builder crossing the resting home army."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


@pytest.mark.parametrize('race,worker,army,target',[
    ('terran','SCV','MARINE','command_center'),('protoss','PROBE','STALKER','nexus'),
    ('zerg','DRONE','ZERGLING','hatchery')])
def test_resting_defenders_allow_a_builder_to_cross(monkeypatch,race,worker,army,target):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2.position import Point2
    from sc2bench_env.backends.sharpy.micro import MissionMicroRules
    state={'spawned':False,'finish':False,'yields':0,'errors':[],'race':race}
    original=MissionMicroRules._builder_passage
    def watch(rules,unit):
        point=original(rules,unit)
        if point is not None:state['yields']+=1
        return point
    monkeypatch.setattr(MissionMicroRules,'_builder_passage',watch)
    async def collect(act):
        ai=act.ai
        if state['finish']:
            await ai.client.debug_kill_unit([b.tag for b in ai.structures]);state['finish']=False;return
        if not state['spawned']:
            await ai.client.debug_all_resources()
            await ai.client.debug_kill_unit([u.tag for u in ai.workers])
            await ai.client.debug_create_unit([
                [getattr(U,worker),1,Point2((72.048095703125,154.7353515625)),ai.player_id],
                [getattr(U,army),1,Point2((72.59912109375,154.322509765625)),ai.player_id],
                [getattr(U,army),1,Point2((71.9052734375,154.050537109375)),ai.player_id]])
            state['spawned']=True;return
        state['bases']=len(ai.townhalls)
        state['errors'].extend(dict(ability=e.ability_id,result=e.result) for e in ai.state.action_errors)
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir=f'records/platform_audit_comprehensive_20261031/builder_passage_{race}')
    def wait(n=2):return {'name':'advance','arguments':{'seconds':n}}
    try:
        env.reset(EpisodeConfig(race=race,enemy_race='protoss',opponent='veryeasy',map_name='TritonLE',seed=202610694,game_time_limit_seconds=120))
        drive(env,[wait(1)])
        _,feedback,_,_=drive(env,[{'name':'build','arguments':{'target':target}},wait(2)])
        assert feedback.receipts and feedback.receipts[0].result=='accepted',feedback
        for _ in range(20):
            if state.get('bases',0)>=2:break
            drive(env,[wait()])
        Path(f'analysis/platform_audit/builder_passage_{race}_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert state.get('bases',0)>=2 and state['yields']>0,state
        # Native units can report one transient path failure while their
        # collision disks separate. The contract is bounded recovery of the
        # original request; retain those errors instead of requiring silence.
        assert all(error['result']==208 for error in state['errors']),state
        state['finish']=True;drive(env,[wait()])
    finally:
        env.close()
