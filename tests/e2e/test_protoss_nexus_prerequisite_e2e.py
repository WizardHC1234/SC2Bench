"""Native Gateway/Forge availability disappears with the last Nexus, not its Pylon."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


def test_nexus_loss_and_restoration_controls_native_and_public_build_availability(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2.ids.ability_id import AbilityId as A
    state={'spawned':False,'kill':False,'rebuild':False,'finish':False,'samples':[]}
    async def collect(act):
        ai=act.ai
        if state['finish']:
            await ai.client.debug_kill_unit([s.tag for s in ai.structures]);state['finish']=False;return
        if not state['spawned']:
            await ai.client.debug_create_unit([[U.PYLON,1,ai.start_location.towards(ai.game_info.map_center,7),ai.player_id]])
            state['spawned']=True;return
        if state['kill']:
            await ai.client.debug_kill_unit([s.tag for s in ai.structures(U.NEXUS)])
            state['kill']=False;return
        if state['rebuild']:
            await ai.client.debug_create_unit([[U.NEXUS,1,ai.start_location,ai.player_id]])
            state['rebuild']=False;return
        if ai.workers:
            abilities=await ai.get_available_abilities(ai.workers.first,ignore_resource_requirements=True)
            state['samples'].append(dict(nexus=len(ai.structures(U.NEXUS).ready),
                pylon=len(ai.structures(U.PYLON).ready),
                gateway=A.PROTOSSBUILD_GATEWAY in abilities,forge=A.PROTOSSBUILD_FORGE in abilities))
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir='records/platform_audit_contract_20261031/nexus_prerequisite')
    def wait(n=1):return {'name':'advance','arguments':{'seconds':n}}
    try:
        env.reset(EpisodeConfig(race='protoss',opponent='veryeasy',map_name='ThunderbirdLE',game_time_limit_seconds=180))
        drive(env,[wait(3)])
        assert state['samples'][-1]['gateway'] and state['samples'][-1]['forge'],state
        state['kill']=True;obs,_,_,_=drive(env,[wait(3)])
        missing=state['samples'][-1]
        assert missing['nexus']==0 and missing['pylon']==1 and not missing['gateway'] and not missing['forge'],state
        assert not {'gateway','forge'} & set(obs.available_targets['build'])
        drive(env,[{'name':'build','arguments':{'target':'gateway'}},
                   {'name':'build','arguments':{'target':'forge'}},wait(3)])
        waiting=env.task_manager.production_priority_summary()
        state['waiting']=waiting
        assert all(r['waiting_for']=='prerequisite:nexus' for r in waiting),waiting
        state['rebuild']=True
        for _ in range(20):
            obs,_,_,_=drive(env,[wait(4)])
            if obs.building.get('gateway',{}).get('under_construction',0) and obs.building.get('forge',{}).get('under_construction',0):break
        state['restored_building']=obs.building
        Path('analysis/platform_audit/nexus_prerequisite_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert any(r['nexus']==1 and r['gateway'] and r['forge'] for r in state['samples']),state
        assert obs.building.get('gateway',{}).get('completed',0)+obs.building.get('gateway',{}).get('under_construction',0)>=1
        assert obs.building.get('forge',{}).get('completed',0)+obs.building.get('forge',{}).get('under_construction',0)>=1
        state['finish']=True;drive(env,[wait(2)])
    finally:
        env.close()
