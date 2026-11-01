"""Three-race native worker defense preserves dedicated roles and still recruits miners."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


@pytest.mark.parametrize('race',['terran','protoss','zerg'])
def test_native_defense_respects_worker_roles_and_recruits_a_released_miner(monkeypatch,race):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sharpy.managers.core.roles import UnitTask as R
    from sc2bench_env.backends.sharpy.defense import PlanZoneDefenseSafe
    state={'spawned':False,'release':False,'finish':False,'calls':0,'violations':[],'recruited':[]}
    original=PlanZoneDefenseSafe.worker_defence
    async def defend(act,*args,**kwargs):
        state['calls']+=1
        before={w.tag:act.roles.unit_role(w) for w in act.ai.workers}
        if state['release'] and len(state.setdefault('miner_checks',[]))<8:
            worker=act.ai.workers.find_by_tag(state['miner'])
            if worker is not None:
                state['miner_checks'].append(dict(role=int(before[worker.tag]),
                    used=worker.tag in act.ai.unit_tags_received_action,
                    orders=[o.ability.id.name for o in worker.orders],
                    queued=[a.ability.name for a in act.ai.actions if a.unit.tag==worker.tag]))
        result=await original(act,*args,**kwargs)
        for w in act.ai.workers:
            after=act.roles.unit_role(w)
            if before[w.tag] in {R.Scouting,R.Building,R.Reserved} and after!=before[w.tag]:
                state['violations'].append(dict(tag=w.tag,before=int(before[w.tag]),after=int(after)))
            if before[w.tag] in {R.Idle,R.Gathering} and after==R.Defending:
                state['recruited'].append(w.tag)
        return result
    monkeypatch.setattr(PlanZoneDefenseSafe,'worker_defence',defend)
    async def collect(act):
        ai=act.ai
        if state['finish']:
            await ai.client.debug_kill_unit([s.tag for s in ai.structures]);state['finish']=False;return
        if not state['spawned']:
            workers=list(ai.workers)
            state['protected']={workers[0].tag:R.Scouting,workers[1].tag:R.Building}
            state['miner']=workers[-1].tag
            await ai.client.debug_create_unit([[U.ZEALOT,2,ai.start_location.towards(ai.game_info.map_center,4),3-ai.player_id]])
            state['spawned']=True
        for w in ai.workers:
            await ai.client.debug_set_unit_value([w.tag],2,1000)
            if w.tag==state['miner'] and state['release']:
                if act.roles.unit_role(w)!=R.Defending:act.roles.set_task(R.Gathering,w)
            else:act.roles.set_task(state['protected'].get(w.tag,R.Reserved),w)
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir=f'records/platform_audit_contract_20261031/worker_defense_{race}')
    def wait():return {'name':'advance','arguments':{'seconds':1}}
    try:
        env.reset(EpisodeConfig(race=race,enemy_race='protoss',opponent='veryeasy',map_name='TritonLE',game_time_limit_seconds=60))
        for _ in range(3):drive(env,[wait()])
        assert state['calls']>0 and not state['violations'] and not state['recruited'],state
        state['release']=True
        for _ in range(7):
            drive(env,[wait()])
            if state['recruited']:break
        Path(f'analysis/platform_audit/worker_defense_{race}_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert not state['violations'] and state['miner'] in state['recruited'],state
        state['finish']=True;drive(env,[wait()])
    finally:
        env.close()
