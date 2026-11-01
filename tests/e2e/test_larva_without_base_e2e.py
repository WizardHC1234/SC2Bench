"""Isolated native loss scenario; not a model match or performance result."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


def test_existing_larva_can_hatch_a_drone_after_townhall_loss(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2.ids.ability_id import AbilityId as A
    from analysis.platform_audit.probe_interface import InterfaceProbe
    InterfaceProbe().install()
    state={'phase':0,'finish':False}
    async def collect(act):
        ai=act.ai
        if state['finish'] and ai.structures:
            await ai.client.debug_kill_unit([u.tag for u in ai.structures])
            state['finish']=False
            return
        if state['phase']==0:
            await ai.client.debug_create_unit([
                [U.SPAWNINGPOOL,1,ai.start_location.offset((-6,0)),ai.player_id],
                [U.OVERLORD,1,ai.start_location,ai.player_id]])
            state['phase']=1
        elif state['phase']==1 and ai.structures(U.SPAWNINGPOOL).ready:
            await ai.client.debug_kill_unit([u.tag for u in ai.townhalls])
            state['phase']=2
        elif state['phase']==2 and not ai.townhalls:
            larvae=ai.units(U.LARVA)
            abilities=await ai.get_available_abilities(larvae)
            state.update(phase=3,larvae=len(larvae),workers_before=len(ai.workers),
                native_abilities=[[a.name for a in row] for row in abilities],
                drone_offered=any(A.LARVATRAIN_DRONE in row for row in abilities))
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir=Path('records/platform_audit_mechanics_20261031/larva_no_base'))
    try:
        env.reset(EpisodeConfig(race='zerg',enemy_race='terran',opponent='veryeasy',
            map_name='KairosJunctionLE',game_time_limit_seconds=90))
        for _ in range(10):
            obs,_,done,_=drive(env,[{'name':'advance','arguments':{'seconds':1}}])
            assert not done
            if state['phase']==3:break
        Path('analysis/platform_audit/larva_no_base_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert state['phase']==3 and state['larvae'] and state['drone_offered'],state
        drive(env,[{'name':'train','arguments':{'target':'drone','count':1}},
                   {'name':'advance','arguments':{'seconds':15}}])
        snapshot=env.backend.snapshot()
        state['workers_after']=snapshot.units.get('drone',0)
        state['training_state']=[dict(target=d.target,state=str(d.state),produced=d.produced,
            waiting_for=d.waiting_for) for d in env.task_manager.demands.values() if d.action=='train']
        Path('analysis/platform_audit/larva_no_base_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert state['workers_after']==state['workers_before']+1,state
        state['finish']=True
        _,_,done,_=drive(env,[{'name':'advance','arguments':{'seconds':2}}])
        assert done
    finally:
        env.close()
