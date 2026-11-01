"""Remote shared vision must not redirect a marching army's objective."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment,ToolCall
from sc2bench_env.interface.config import EpisodeConfig
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


def test_remote_visible_enemy_does_not_pull_a_mixed_army_back(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sharpy.combat.default_micro_methods import DefaultMicroMethods
    if os.environ.get('SC2BENCH_LEGACY_CONTACTS')=='1':
        monkeypatch.setattr(DefaultMicroMethods,'local_enemy_groups',staticmethod(lambda group,groups,values:groups))
        monkeypatch.setattr(DefaultMicroMethods,'local_contact',staticmethod(lambda *_:True))
    state=dict(setup=False,errors=[],frames=[])
    async def collect(act):
        ai=act.ai
        state['errors'].extend(dict(seconds=ai.time,ability=e.ability_id,result=e.result)
                               for e in ai.state.action_errors)
        if not state.get('revealed'):
            await ai.client.debug_show_map()
            state['revealed']=True
            return
        if not state['setup']:
            home=ai.bench_home_gather.home_point()
            # Debug structures still need a valid footprint. Keep the fixed
            # contact behind the march, outside every spawned unit's sight.
            away=ai.start_location.towards(ai.game_info.map_center,-6)
            remote=await ai.find_placement(U.MISSILETURRET,near=away,
                                          max_distance=15,placement_step=1,random_alternative=False)
            assert remote is not None and remote.distance_to(home)>16,(remote,home)
            await ai.client.debug_create_unit([
                [U.STALKER,7,home,ai.player_id],[U.ZEALOT,5,home,ai.player_id],
                [U.IMMORTAL,1,home,ai.player_id],[U.MISSILETURRET,1,remote,3-ai.player_id]])
            state.update(setup=True,home=list(home),remote=list(remote))
            return
        soldiers=ai.units({U.STALKER,U.ZEALOT,U.IMMORTAL})
        # A fixed, durable remote contact isolates targeting from the built-in
        # AI's aircraft movement. This fixture never changes model observations.
        turrets=ai.enemy_structures(U.MISSILETURRET)
        for turret in turrets:
            await ai.client.debug_set_unit_value([turret.tag],2,turret.health_max)
        state['frames'].append(dict(seconds=ai.time,
            units=[dict(tag=u.tag,type=u.type_id.name,point=list(u.position),
                        assigned=u.tag in getattr(ai,'bench_combat_tags',set()),
                        orders=[o.ability.id.name for o in u.orders]) for u in soldiers],
            enemy_visible=any(e.is_visible and not e.is_memory for e in turrets)))
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir='records/platform_audit_contract_20261101/local_contact_march')
    def step(calls=(),seconds=3):
        turn=env.begin_tool_turn()
        for name,args in [*calls,('advance',{'seconds':seconds})]:
            result=turn.call(ToolCall(name,args));assert result.status!='rejected',result
        obs,feedback,done,info=env.step(turn.finish());assert not done,info
        return obs,feedback
    try:
        obs=env.reset(EpisodeConfig(race='protoss',enemy_race='terran',opponent='veryeasy',
                                  map_name='AcropolisLE',seed=202611304,game_time_limit_seconds=100))
        for _ in range(6):
            obs,_=step()
            if (obs.own_forces.free.get('stalker',0)>=7 and obs.own_forces.free.get('zealot',0)>=3
                    and obs.own_forces.free.get('immortal',0)>=1):break
        target=next(z['zone_id'] for z in obs.zone_state if z['zone_role']=='enemy_main')
        state['target']=target
        selected={n:min(count,obs.own_forces.free.get(n,0))
                  for n,count in {'stalker':7,'zealot':5,'immortal':1}.items()}
        state['requested_members']=sum(selected.values())
        obs,feedback=step([('dispatch_group',dict(source='group_0',order='attack',target=target,units=selected))])
        assert feedback.receipts[0].result=='accepted',feedback
        for _ in range(10):step()
        frame=state['frames'][-1]
        hx,hy=state['home']
        distances=[((u['point'][0]-hx)**2+(u['point'][1]-hy)**2)**.5 for u in frame['units'] if u['assigned']]
        state['displacements']=distances
        assert frame['enemy_visible'],state
        assert len(distances)==state['requested_members'],state
        assert min(distances)>30,state
        assert not state['errors'],state
    finally:
        state['record_directory']=str(env.record_path)
        suffix='before' if os.environ.get('SC2BENCH_LEGACY_CONTACTS')=='1' else 'fixed'
        Path(f'analysis/platform_audit/local_contact_march_{suffix}.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        env.close()
