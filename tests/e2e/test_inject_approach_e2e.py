"""Real path/ability control for the Queen/Hatchery positions from round 66."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


@pytest.mark.parametrize('crowded',[False,True,'hydra'])
def test_inject_from_recorded_positions_without_a_blocking_army(monkeypatch,crowded):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2.ids.buff_id import BuffId as B
    from sc2.position import Point2
    state={'spawned':False,'charged':False,'finish':False,'frames':[]}
    async def collect(act):
        ai=act.ai
        if state['finish']:
            await ai.client.debug_kill_unit([s.tag for s in ai.structures]);state['finish']=False;return
        if not state['spawned']:
            units=[[U.HATCHERY,1,Point2((82.5,160.5)),ai.player_id],
                [U.QUEEN,1,Point2((92.851074,152.523438)),ai.player_id]]
            if crowded == 'hydra':
                fixture=json.loads(Path('tests/fixtures/inject_hydra_crowd.json').read_text())
                units=[[U.HATCHERY,1,Point2(fixture['hatchery']),ai.player_id],
                       [U.QUEEN,1,Point2(fixture['queen']),ai.player_id]]
                units += [[U.HYDRALISK,1,Point2(p),ai.player_id] for p in fixture['hydralisks']]
            elif crowded:
                units += [[U.ROACH,1,Point2(p),ai.player_id] for p in [
                    (89.1543,151.2180),(91.0767,153.9080),(86.1653,156.0366),
                    (91.8398,153.3616),(85.7947,156.9680),(89.6440,152.1970),
                    (84.9961,155.2852),(90.9634,152.2576),(85.4021,154.2256),
                    (90.3799,152.9924),(88.3501,150.7317)]]
                units += [[U.ZERGLING,1,Point2(p),ai.player_id] for p in [
                    (90.0032,153.7861),(89.2341,153.7083),(89.6204,154.3584)]]
            await ai.client.debug_create_unit(units)
            state['spawned']=True;return
        for queen in ai.units(U.QUEEN):
            if not state['charged']:
                await ai.client.debug_set_unit_value([queen.tag],1,100);state['charged']=True
            state['frames'].append(dict(time=float(ai.time),position=list(queen.position),energy=float(queen.energy),
                orders=[o.ability.id.name for o in queen.orders]))
        state['buffs']=[dict(position=list(h.position),injected=B.QUEENSPAWNLARVATIMER in h.buffs) for h in ai.townhalls]
        state['injected']=any(h['injected'] for h in state['buffs'])
    _install(monkeypatch,collect)
    label='hydra' if crowded == 'hydra' else 'crowded' if crowded else 'clear'
    env=Environment('sharpy',record_dir=f'records/platform_audit_contract_20261031/inject_path_{label}')
    def wait(n=1):return {'name':'advance','arguments':{'seconds':n}}
    try:
        env.reset(EpisodeConfig(race='zerg',enemy_race='protoss',opponent='veryeasy',map_name='TritonLE',seed=202610954,game_time_limit_seconds=90))
        drive(env,[wait(6)])
        drive(env,[{'name':'inject_larva','arguments':{}},wait()])
        for _ in range(25):
            if state.get('injected'):break
            drive(env,[wait()])
        state['active']=[r for r in env.task_manager.production_priority_summary() if r['action']=='inject_larva']
        Path(f'analysis/platform_audit/inject_path_{label}_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert state.get('injected'),state
        state['finish']=True;drive(env,[wait(2)])
    finally:
        env.close()
