"""A unit crossing a lowered Depot must not trigger repeated rejected raises."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


def test_depot_raises_after_occupant_clears_without_rejected_orders(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2.ids.ability_id import AbilityId as A
    from sharpy.managers.core.roles import UnitTask
    state={'spawned':False,'finish':False,'blocked_frames':0,'raised':False,'errors':[]}
    async def collect(act):
        ai=act.ai
        if state['finish']:
            await ai.client.debug_kill_unit([b.tag for b in ai.structures]);state['finish']=False;return
        if not state['spawned']:
            point=ai.start_location.towards(ai.game_info.map_center,12).rounded
            await ai.client.debug_create_unit([[U.SUPPLYDEPOTLOWERED,1,point,ai.player_id],
                [U.SIEGETANKSIEGED,1,point,ai.player_id],
                [U.SIEGETANKSIEGED,1,point.offset((4,0)),2 if ai.player_id==1 else 1]])
            state.update(spawned=True,start=float(ai.time));return
        for error in ai.state.action_errors:
            if error.ability_id==A.MORPH_SUPPLYDEPOT_RAISE.value:
                state['errors'].append(error.result)
        for tank in ai.units(U.SIEGETANKSIEGED):act.roles.set_task(UnitTask.Reserved,tank)
        elapsed=ai.time-state['start']
        if elapsed<3:
            if ai.units(U.SIEGETANKSIEGED) and ai.structures(U.SUPPLYDEPOTLOWERED):state['blocked_frames']+=1
        elif not state.get('cleared'):
            await ai.client.debug_kill_unit([u.tag for u in ai.units(U.SIEGETANKSIEGED)])
            state['cleared']=True
        elif ai.structures(U.SUPPLYDEPOT):state['raised']=True
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir='records/platform_audit_comprehensive_20261031/depot_occupancy')
    try:
        env.reset(EpisodeConfig(opponent='veryeasy',map_name='KairosJunctionLE',game_time_limit_seconds=60))
        for _ in range(9):drive(env,[{'name':'advance','arguments':{'seconds':1}}])
        assert state['blocked_frames']>=2 and state['raised'] and not state['errors'],state
        Path('analysis/platform_audit/depot_occupancy_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        state['finish']=True;drive(env,[{'name':'advance','arguments':{'seconds':2}}])
    finally:
        env.close()
