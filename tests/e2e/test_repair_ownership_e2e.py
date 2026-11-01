"""Automatic repair cannot commandeer scouts, builders or reserved workers."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


def test_repair_preserves_dedicated_workers_and_uses_a_released_worker(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2.unit import Unit
    from sharpy.managers.core.roles import UnitTask
    state={'spawned':False,'armed':False,'release':False,'finish':False,'calls':0,'violations':[]}
    live={};original=Unit.repair
    def repair(worker,target,*args,**kwargs):
        act=live.get('act')
        if act is not None:
            role=act.roles.unit_role(worker)
            state['calls']+=1
            if role not in {UnitTask.Idle,UnitTask.Gathering} or worker.is_constructing_scv or worker.tag in act.ai.unit_tags_received_action:
                state['violations'].append({'tag':worker.tag,'role':int(role)})
        return original(worker,target,*args,**kwargs)
    monkeypatch.setattr(Unit,'repair',repair)
    async def collect(act):
        ai=act.ai;live['act']=act
        if state['finish']:
            await ai.client.debug_kill_unit([b.tag for b in ai.structures]);state['finish']=False;return
        if not state['spawned']:
            await ai.client.debug_all_resources()
            await ai.client.debug_create_unit([[U.BUNKER,1,ai.start_location.towards(ai.game_info.map_center,7),ai.player_id]])
            state.update(spawned=True,eligible=[w.tag for w in list(ai.workers)[:2]])
        for w in ai.workers:
            if w.tag not in state['eligible']:
                if state['release']:
                    act.roles.clear_task(w);state['eligible'].append(w.tag);state['release']=False
                else:act.roles.set_task(UnitTask.Reserved,w)
        if state['armed'] and not state.get('damaged') and ai.structures(U.BUNKER):
            bunker=ai.structures(U.BUNKER).first
            await ai.client.debug_set_unit_value([bunker.tag],2,150)
            state['damaged']=True
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir='records/platform_audit_contract_20261031/repair_ownership')
    def wait(n=2):return {'name':'advance','arguments':{'seconds':n}}
    try:
        env.reset(EpisodeConfig(opponent='veryeasy',map_name='KairosJunctionLE',game_time_limit_seconds=90))
        drive(env,[wait()]);state['armed']=True
        obs,feedback,_,_=drive(env,[{'name':'scout','arguments':{'route':'all'}},
            {'name':'build','arguments':{'target':'supply_depot'}},wait(8)])
        assert all(r.result=='accepted' for r in feedback.receipts),feedback
        state['calls_before_release']=state['calls']
        assert not state['violations'] and state['calls']==0,state
        state['release']=True
        for _ in range(8):
            drive(env,[wait(1)])
            if state['calls']>0:break
        assert state['calls']>0 and not state['violations'],state
        Path('analysis/platform_audit/repair_ownership_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        state['finish']=True;drive(env,[wait()])
    finally:
        env.close()
