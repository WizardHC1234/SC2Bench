"""A tank near the station on the ramp must finish moving onto the plateau."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


def test_air_vision_does_not_toggle_a_tank_on_the_wrong_side_of_the_station(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2.ids.ability_id import AbilityId as A
    from sc2.position import Point2
    from sharpy.managers.core.roles import UnitTask
    from sc2bench_env.backends.sharpy.micro import MicroDefendTanks
    from sc2bench_env.backends.sharpy.holding import high_ground_side
    state={'phase':0,'finish':False,'off_plateau_deployments':0,'changes':[],'air_waves':0}
    original=MicroDefendTanks.unit_solve_combat
    def watch(micro,unit,command):
        result=original(micro,unit,command)
        boundary=getattr(getattr(micro,'rules',None),'boundary',None)
        if result.ability==A.SIEGEMODE_SIEGEMODE and callable(boundary) and not boundary(unit.position):
            if not micro._local_ground_enemies(unit).exists:
                state['off_plateau_deployments']+=1
        return result
    monkeypatch.setattr(MicroDefendTanks,'unit_solve_combat',watch)
    initial=Point2((125.841796875,54.0625));air=initial.offset((0,7))
    async def collect(act):
        ai=act.ai
        if state['finish']:
            if ai.structures:await ai.client.debug_kill_unit([b.tag for b in ai.structures])
            state['finish']=False;return
        if state['phase']==0:
            await ai.client.debug_create_unit([
                [U.COMMANDCENTER,1,act.zone_manager.own_natural.center_location,ai.player_id],
                [U.SIEGETANKSIEGED,1,initial,ai.player_id],
                [U.OVERLORD,1,air,2 if ai.player_id==1 else 1]])
            state['phase']=1;return
        tanks=ai.units.of_type({U.SIEGETANK,U.SIEGETANKSIEGED})
        if not tanks:return
        tank=tanks.first;natural=act.zone_manager.own_natural
        if state['phase']==1:
            act.roles.set_task(UnitTask.Reserved,tank)
            if natural.our_townhall is None or not natural.our_townhall.is_ready:return
            station=ai.bench_home_gather.home_point()
            state.update(phase=2,start=float(ai.time),station=list(station),
                initial_position=list(tank.position),initial_distance=tank.distance_to(station),
                initial_allowed=high_ground_side(ai,natural,station,tank.position))
            act.roles.clear_task(tank)
        if state['phase']==2:
            wave=int((ai.time-state['start'])//3)
            overlords=ai.enemy_units(U.OVERLORD)
            if wave%2 and overlords:
                await ai.client.debug_kill_unit([u.tag for u in overlords]);state['air_waves']+=1
            elif not wave%2 and not overlords:
                await ai.client.debug_create_unit([[U.OVERLORD,1,air,2 if ai.player_id==1 else 1]])
                state['air_waves']+=1
            allowed=high_ground_side(ai,natural,Point2(state['station']),tank.position)
            if not state['changes'] or state['changes'][-1]['type']!=tank.type_id.name:
                state['changes'].append(dict(seconds=float(ai.time),type=tank.type_id.name,
                    position=list(tank.position),allowed=allowed))
            state.update(seconds=float(ai.time),final_allowed=allowed,final_type=tank.type_id.name)
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir=Path('records/platform_audit_comprehensive_20261031/station_plateau'))
    try:
        env.reset(EpisodeConfig(race='terran',enemy_race='protoss',opponent='veryeasy',
            map_name='TritonLE',seed=202610681,game_time_limit_seconds=90))
        for _ in range(26):
            drive(env,[{'name':'advance','arguments':{'seconds':1}}])
            if state.get('seconds',0)-state.get('start',999)>20:break
        Path('analysis/platform_audit/station_plateau_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert state['phase']==2 and state['initial_distance']<=5 and not state['initial_allowed'],state
        assert state['off_plateau_deployments']==0,state
        assert state['final_allowed'] and state['final_type']=='SIEGETANKSIEGED',state
        assert len(state['changes'])<=3,state
        state['finish']=True;drive(env,[{'name':'advance','arguments':{'seconds':2}}])
    finally:env.close()
