"""Public tumor requests confirm both paid Queen planting and free tumor spreading."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


def test_public_tumor_request_can_use_queen_and_then_free_tumor(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2.ids.ability_id import AbilityId as A
    from sc2.unit import Unit
    state={'spawned':False,'charged':False,'finish':False,'drain':False,'free_ready':False,'casts':[],'tumors':0}
    original=Unit.__call__
    def cast(unit,ability,*args,**kwargs):
        if ability in {A.BUILD_CREEPTUMOR_QUEEN,A.BUILD_CREEPTUMOR_TUMOR}:
            state['casts'].append({'actor':unit.type_id.name,'ability':ability.name})
        return original(unit,ability,*args,**kwargs)
    monkeypatch.setattr(Unit,'__call__',cast)
    async def collect(act):
        ai=act.ai
        if state['finish']:
            await ai.client.debug_kill_unit([b.tag for b in ai.structures]);state['finish']=False;return
        if not state['spawned']:
            await ai.client.debug_create_unit([[U.QUEEN,1,ai.start_location.towards(ai.game_info.map_center,6),ai.player_id]])
            state['spawned']=True;return
        for queen in ai.units(U.QUEEN):
            if not state['charged']:
                await ai.client.debug_set_unit_value([queen.tag],1,100);state['charged']=True
            elif state['drain']:
                await ai.client.debug_kill_unit([queen.tag])
        tumors=ai.structures.of_type({U.CREEPTUMOR,U.CREEPTUMORQUEEN,U.CREEPTUMORBURROWED})
        state['tumors']=len(tumors)
        for tumor in tumors:
            if tumor.type_id==U.CREEPTUMORBURROWED and A.BUILD_CREEPTUMOR_TUMOR in await ai.get_available_abilities(tumor):
                state['free_ready']=True
        from sc2bench_env.backends.sharpy.races.zerg_production import read_ability_facts
        state['ability_facts']=read_ability_facts(ai,{},None)
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir='records/platform_audit_contract_20261031/tumor_public')
    def wait(n=1):return {'name':'advance','arguments':{'seconds':n}}
    try:
        env.reset(EpisodeConfig(race='zerg',opponent='veryeasy',map_name='KairosJunctionLE',game_time_limit_seconds=100))
        drive(env,[wait(2)])
        drive(env,[{'name':'spawn_creep_tumor','arguments':{}},wait(3)])
        for _ in range(8):
            if state['tumors']>=1:break
            drive(env,[wait()])
        assert state['tumors']>=1,state
        for _ in range(30):
            if state['free_ready']:break
            drive(env,[wait()])
        assert state['free_ready'],state
        state['drain']=True;drive(env,[wait()])
        drive(env,[{'name':'spawn_creep_tumor','arguments':{}},wait(3)])
        for _ in range(8):
            if state['tumors']>=2:break
            drive(env,[wait()])
        Path('analysis/platform_audit/tumor_public_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert state['tumors']>=2,state
        assert any(c['ability']==A.BUILD_CREEPTUMOR_QUEEN.name for c in state['casts']),state
        assert any(c['ability']==A.BUILD_CREEPTUMOR_TUMOR.name for c in state['casts']),state
        assert state['ability_facts']['queen_count']==0,state
        assert state['ability_facts']['spawn_creep_tumor_ready']==0,state
        assert not any(d.action=='spawn_creep_tumor' for d in env.task_manager.active_demands()),state
        state['finish']=True;drive(env,[wait(2)])
    finally:
        env.close()
