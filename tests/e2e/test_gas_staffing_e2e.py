"""Two gas structures at one base through public tools, with native staffing."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


@pytest.mark.parametrize('race,worker,target',[
    ('protoss','PROBE','assimilator'),('terran','SCV','refinery'),('zerg','DRONE','extractor')])
def test_two_gas_buildings_and_staffing_at_one_base(monkeypatch,race,worker,target):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from analysis.platform_audit.probe_interface import InterfaceProbe
    InterfaceProbe().install()
    state={'race':race,'spawned':False,'finish':False}
    async def collect(act):
        ai=act.ai
        if state['finish']:
            if ai.structures:
                await ai.client.debug_kill_unit([u.tag for u in ai.structures])
            state['finish']=False
            return
        if not state['spawned']:
            state['workers_initial']=len(ai.workers)
            await ai.client.debug_all_resources()
            await ai.client.debug_create_unit([[getattr(U,worker),16-len(ai.workers),
                                                ai.start_location.towards(ai.game_info.map_center,4),ai.player_id]])
            state['spawned']=True
        state.update(seconds=float(ai.time),bases=len(ai.townhalls),workers=len(ai.workers),
            available_workers=act.roles.free_workers.amount,
            native_worker_supply=ai.state.response_observation.observation.player_common.food_workers,
            gas=[dict(position=list(b.position),ready=b.is_ready,assigned=b.assigned_harvesters,
                      ideal=b.ideal_harvesters,distance_to_main=b.distance_to(ai.start_location))
                 for b in ai.gas_buildings])
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir=Path('records/platform_audit_mechanics_20261031')/f'two_gas_{race}')
    def advance(seconds):
        return drive(env,[{'name':'advance','arguments':{'seconds':seconds}}])
    destination=Path('analysis/platform_audit')/f'two_gas_{race}_native.json'
    try:
        env.reset(EpisodeConfig(race=race,enemy_race='terran',opponent='veryeasy',
            map_name='AcropolisLE',game_time_limit_seconds=150))
        advance(2)
        drive(env,[{'name':'build','arguments':{'target':target}},
                   {'name':'build','arguments':{'target':target}},
                   {'name':'advance','arguments':{'seconds':35}}])
        for _ in range(16):
            if len(state['gas'])==2 and all(b['ready'] and b['assigned']>=3 for b in state['gas']):break
            advance(5)
        destination.write_text(json.dumps(state,indent=2),encoding='utf8')
        assert state['bases']==1 and len(state['gas'])==2,state
        assert all(b['ready'] and b['assigned']>=3 and b['distance_to_main']<15 for b in state['gas']),state
        state['finish']=True
        advance(2)
    finally:
        env.close()
