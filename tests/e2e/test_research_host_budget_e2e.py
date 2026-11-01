"""Busy research cannot reserve the only minerals needed by a ready worker request."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


@pytest.mark.parametrize('race,kind,first,second,worker',[
    ('terran','ENGINEERINGBAY','infantry_weapons_1','infantry_armor_1','scv'),
    ('protoss','FORGE','ground_weapons_1','ground_armor_1','probe'),
    ('zerg','EVOLUTIONCHAMBER','melee_attacks_1','ground_carapace_1','drone')])
def test_busy_research_does_not_starve_worker_production(monkeypatch,race,kind,first,second,worker):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sharpy.managers.core.roles import UnitTask
    state={'spawned':False,'armed':False,'frozen':False,'finish':False,'race':race}
    stopped=set()
    async def collect(act):
        ai=act.ai
        if state['finish']:
            await ai.client.debug_kill_unit([b.tag for b in ai.structures]);state['finish']=False;return
        if not state['spawned']:
            point=ai.start_location.towards(ai.game_info.map_center,7)
            rows=[[getattr(U,kind),1,point,ai.player_id]]
            gas_kind={'terran':U.REFINERY,'protoss':U.ASSIMILATOR,'zerg':U.EXTRACTOR}[race]
            rows.extend([gas_kind,1,g.position,ai.player_id] for g in ai.vespene_geyser.closest_n_units(ai.start_location,2))
            if race=='protoss':rows.append([U.PYLON,1,point.offset((4,0)),ai.player_id])
            await ai.client.debug_create_unit(rows);state['spawned']=True
        if state['armed'] and ai.minerals>=50 and not state['frozen']:
            state.update(frozen=True,bank=int(ai.minerals),workers_before=int(ai.supply_workers))
        if state['frozen']:
            for w in ai.workers:
                if w.tag not in stopped:
                    w.stop();stopped.add(w.tag)
                act.roles.set_task(UnitTask.Reserved,w)
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir=f'records/platform_audit_contract_20261031/research_host_{race}')
    def wait(n=2):return {'name':'advance','arguments':{'seconds':n}}
    def save():Path(f'analysis/platform_audit/research_host_{race}_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
    try:
        env.reset(EpisodeConfig(race=race,opponent='veryeasy',map_name='KairosJunctionLE',game_time_limit_seconds=240))
        drive(env,[wait(2)])
        obs,_,_,_=drive(env,[{'name':'train','arguments':{'target':worker,'count':3}},
                            {'name':'research','arguments':{'target':first}},wait()])
        for _ in range(55):
            if obs.research.get(first)=='in_progress':break
            obs,_,_,_=drive(env,[wait()])
        assert obs.research.get(first)=='in_progress',obs.research
        state['armed']=True
        for _ in range(20):
            obs,_,_,_=drive(env,[wait(1)])
            if state['frozen']:break
        assert state['frozen'] and 50<=state['bank']<150,state
        obs,_,_,_=drive(env,[{'name':'research','arguments':{'target':second}},
                            {'name':'train','arguments':{'target':worker,'count':1}},wait(2)])
        state['blocked_research']=obs.research.get(second)
        state['worker_training']=obs.training.get(worker);save()
        assert state['blocked_research']=='waiting_for:producer_busy',state
        for _ in range(8):obs,_,_,_=drive(env,[wait()])
        state['workers_after']=obs.own_forces.workers.get(worker,0);save()
        assert state['workers_after']==state['workers_before']+1,state
        assert obs.research[first]=='in_progress',obs.research
        state['finish']=True;drive(env,[wait()])
    finally:
        env.close()
