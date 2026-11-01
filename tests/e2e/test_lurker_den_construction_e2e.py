"""Current native Lurker Den construction and its distinct Hydralisk Den prerequisite."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


def test_lurker_den_uses_a_drone_and_does_not_replace_hydralisk_den(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    state={'spawned':False,'finish':False,'destroy_hydra':False,'raise_tier':False,'errors':[]}
    async def collect(act):
        ai=act.ai
        if state['finish']:
            await ai.client.debug_kill_unit([b.tag for b in ai.structures]);state['finish']=False;return
        if not state['spawned']:
            await ai.client.debug_all_resources()
            await ai.client.debug_create_unit([[U.LAIR,1,act.zone_manager.own_natural.center_location,ai.player_id],
                [U.HYDRALISKDEN,1,ai.start_location.towards(ai.game_info.map_center,7),ai.player_id]])
            data=ai._game_data.units[U.LURKERDENMP.value]
            cost=ai.calculate_cost(U.LURKERDENMP)
            state.update(spawned=True,native_creation=data.creation_ability.exact_id.name,
                native_cost={'minerals':cost.minerals,'vespene':cost.vespene})
        if state['destroy_hydra']:
            await ai.client.debug_kill_unit([b.tag for b in ai.structures(U.HYDRALISKDEN)])
            state['destroy_hydra']=False
        if state['raise_tier']:
            await ai.client.debug_kill_unit([b.tag for b in ai.structures(U.LAIR)])
            await ai.client.debug_create_unit([[U.HIVE,1,act.zone_manager.own_natural.center_location,ai.player_id]])
            state['raise_tier']=False
        state['errors'].extend(dict(ability=e.ability_id,result=e.result) for e in ai.state.action_errors)
        dens=ai.structures(U.LURKERDENMP).ready
        if dens:
            state['den_abilities']=[a.name for a in await ai.get_available_abilities(dens.first)]
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir='records/platform_audit_contract_20261031/lurker_den')
    def wait(n=2):return {'name':'advance','arguments':{'seconds':n}}
    def save():Path('analysis/platform_audit/lurker_den_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
    try:
        env.reset(EpisodeConfig(race='zerg',opponent='veryeasy',map_name='KairosJunctionLE',game_time_limit_seconds=200))
        obs,_,_,_=drive(env,[wait(2)])
        state['workers_before']=obs.own_forces.workers.get('drone',0)
        drive(env,[{'name':'build','arguments':{'target':'lurker_den'}},wait()])
        for _ in range(45):
            obs,_,_,_=drive(env,[wait()])
            if obs.building.get('lurker_den',{}).get('completed')==1:break
        state.update(den=obs.building.get('lurker_den'),hydra=obs.building.get('hydralisk_den'),workers_after=obs.own_forces.workers.get('drone',0));save()
        assert state['den'] and state['den'].get('completed')==1,state
        assert state['hydra']['completed']==1 and state['workers_after']==state['workers_before']-1,state
        state['tier2_den_abilities']=list(state.get('den_abilities',[]))
        state['range_available_before_hive']='lurker_range' in obs.available_targets['research']
        state['raise_tier']=True
        obs,_,_,_=drive(env,[wait(3)])
        state['tier3_den_abilities']=list(state.get('den_abilities',[]));save()
        assert not any('LURKER' in a and 'RANGE' in a for a in state['tier2_den_abilities']),state
        assert any('LURKER' in a and 'RANGE' in a for a in state['tier3_den_abilities']),state
        assert not state['range_available_before_hive'],state
        state['destroy_hydra']=True
        obs,_,_,_=drive(env,[wait()])
        state['hydralisk_available_without_den']='hydralisk' in obs.available_targets['train'];save()
        assert not state['hydralisk_available_without_den'],state
        state['finish']=True;drive(env,[wait()])
    finally:
        env.close()
