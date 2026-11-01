"""Reproduce expansion orders issued before the site enters worker vision."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


def test_worker_can_start_requested_expansion_outside_initial_vision(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    state={'phase':0,'finish':False,'samples':[],'errors':[]}
    async def collect(act):
        ai=act.ai
        if state['finish']:
            await ai.client.debug_kill_unit([b.tag for b in ai.structures]);state['finish']=False;return
        if state['phase']==0:
            target=act.zone_manager.own_natural.center_location
            origin=target.towards(ai.start_location,12)
            await ai.client.debug_all_resources()
            await ai.client.debug_kill_unit([u.tag for u in ai.workers])
            await ai.client.debug_create_unit([[U.DRONE,1,origin,ai.player_id]])
            state.update(phase=1,target=list(target),origin=list(origin));return
        state['bases']=len(ai.townhalls)
        if len(state['samples'])<60:
            from sc2.position import Point2
            state['samples'].append(dict(seconds=float(ai.time),visible=ai.is_visible(Point2(state['target'])),
                workers=[dict(position=list(u.position),orders=[str(o.ability.id) for o in u.orders]) for u in ai.workers]))
        state['errors'].extend(dict(ability=e.ability_id,result=e.result) for e in ai.state.action_errors)
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir='records/platform_audit_comprehensive_20261031/fog_expansion')
    def wait(n=2):return {'name':'advance','arguments':{'seconds':n}}
    try:
        env.reset(EpisodeConfig(race='zerg',enemy_race='protoss',opponent='veryeasy',map_name='TritonLE',seed=202610694,game_time_limit_seconds=90))
        drive(env,[wait(1)])
        obs,feedback,_,_=drive(env,[{'name':'build','arguments':{'target':'hatchery'}},wait(2)])
        assert feedback.receipts and feedback.receipts[0].result=='accepted',feedback
        for _ in range(12):
            if state.get('bases',0)>=2:break
            drive(env,[wait()])
        Path('analysis/platform_audit/fog_expansion_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert state.get('bases',0)>=2,state
        state['finish']=True;drive(env,[wait()])
    finally:
        env.close()
