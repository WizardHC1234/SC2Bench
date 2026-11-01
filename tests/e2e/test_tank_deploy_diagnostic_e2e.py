"""Native deployment checks at the positions captured in the audit game."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark = pytest.mark.skipif(os.environ.get('SC2BENCH_E2E') != '1', reason='Requires SC2')


def test_tank_deployment_at_observed_positions(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2.ids.ability_id import AbilityId as A
    from sc2.position import Point2
    from sharpy.managers.core.roles import UnitTask
    state = dict(case=0, started=None, rows=[], errors=[])
    cases = [((144.28833, 121.90942), False), ((144.28833, 121.90942), True), ((139.5, 120.5), False)]

    async def collect(act):
        ai = act.ai
        for error in ai.state.action_errors:
            if error.ability_id == A.SIEGEMODE_SIEGEMODE.value:
                state['errors'].append(dict(seconds=ai.time, result=error.result, tag=error.unit_tag))
        tanks = ai.units({U.SIEGETANK, U.SIEGETANKSIEGED})
        for tank in tanks:
            act.roles.set_task(UnitTask.Reserved, tank)
        if state['case'] >= len(cases):
            return
        point, crowded = cases[state['case']]
        if state['started'] is None:
            if crowded:
                await ai.client.debug_create_unit([
                    [U.SIEGETANK, 1, Point2((143.48486, 120.87695)), ai.player_id],
                    [U.SIEGETANK, 1, Point2((143.70679, 122.55103)), ai.player_id]])
            else:
                await ai.client.debug_create_unit([[U.SIEGETANK, 1, Point2(point), ai.player_id]])
            state['started'] = ai.time
            state['commanded'] = False
            state['neighbors_deployed'] = False
            state['actor_spawned'] = not crowded
            state['actor_moved'] = False
            return
        elapsed = ai.time - state['started']
        if crowded and not state['neighbors_deployed']:
            for tank in tanks:
                tank(A.SIEGEMODE_SIEGEMODE)
            state['neighbors_deployed'] = True
            return
        if crowded and elapsed > 4 and not state['actor_spawned']:
            await ai.client.debug_create_unit([[U.SIEGETANK, 1, Point2(point).offset((4,3)), ai.player_id]])
            state['actor_spawned'] = True
            return
        mobile = tanks.of_type(U.SIEGETANK)
        if crowded and mobile and not state['actor_moved']:
            mobile.first.move(Point2(point))
            state['actor_moved'] = True
        if not state['commanded'] and mobile and (not crowded or elapsed > 12):
            tank = mobile.first
            state['tag'] = tank.tag
            state['initial'] = list(tank.position)
            state['height_grid'] = [[dx,dy,ai.get_terrain_height(tank.position.offset((dx,dy)))]
                for dx,dy in [(0,0),(-1,0),(1,0),(0,-1),(0,1)]]
            tank(A.SIEGEMODE_SIEGEMODE)
            state['commanded'] = True
        if elapsed > (25 if crowded else 12):
            tank = tanks.find_by_tag(state.get('tag'))
            state['rows'].append(dict(case=state['case'], crowded=crowded, requested=point,
                initial=state['initial'], final=list(tank.position), form=tank.type_id.name,
                height_grid=state['height_grid'], errors=[e for e in state['errors'] if e['tag']==tank.tag]))
            await ai.client.debug_kill_unit([u.tag for u in tanks])
            state['case'] += 1
            state['started'] = None

    _install(monkeypatch, collect)
    env = Environment('sharpy', record_dir='records/platform_audit_contract_20261031/tank_deploy_control')
    try:
        env.reset(EpisodeConfig(race='terran', enemy_race='zerg', opponent='veryeasy',
            map_name='WorldofSleepersLE', game_time_limit_seconds=90))
        for _ in range(34):
            drive(env, [{'name':'advance','arguments':{'seconds':2}}])
        Path('analysis/platform_audit/tank_deploy_control_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert len(state['rows']) == 3, state
    finally:
        env.close()
