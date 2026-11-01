"""Controlled native placement check, separate from full model matches."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


@pytest.mark.parametrize('race,soldier,base,name',[
    ('protoss','STALKER','NEXUS','nexus'),
    ('terran','SIEGETANKSIEGED','COMMANDCENTER','command_center'),
    ('zerg','ROACH','HATCHERY','hatchery'),
])
def test_stationed_army_clears_expansion_site(monkeypatch,race,soldier,base,name):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2bench_env.backends.sharpy.acts import ActCombatMission
    from sc2bench_env.backends.sharpy.holding import overlaps_townhall_site
    from analysis.platform_audit.probe_interface import InterfaceProbe
    from sharpy.managers.core.roles import UnitTask
    InterfaceProbe().install()
    state={'race':race,'phase':0,'move':False,'finish':False}
    runtime={}
    destination=Path('analysis/platform_audit')/f'expansion_station_{race}_native.json'
    async def collect(act):
        ai=act.ai
        if state['finish']:
            if ai.structures:
                await ai.client.debug_kill_unit([u.tag for u in ai.structures])
            state['finish']=False
            return
        center=act.zone_manager.own_natural.center_location
        ability=ai._game_data.units[getattr(U,base).value].creation_ability
        if state['phase']==0:
            state['center']=list(center)
            await ai.client.debug_all_resources()
            await ai.client.debug_create_unit([[getattr(U,soldier),8,center,ai.player_id]])
            state['phase']=1
            return
        troops=ai.units.of_type({getattr(U,soldier),U.SIEGETANK} if race=='terran' else {getattr(U,soldier)})
        if state['phase']==1 and len(troops)==8:
            registry=ai.zone_registry
            zone_id=next(z for z in registry.zone_ids if registry.center_for(z)==tuple(center))
            mission=ActCombatMission('defend',zone_id,{'tank' if race=='terran' else soldier.lower():8})
            await mission.start(act.knowledge)
            mission._bound=True
            mission._tags=[u.tag for u in troops]
            mission._reserved_tags().update(mission._tags)
            runtime['mission']=mission
            state['phase']=2
            for unit in troops:
                unit.hold_position()
            return
        if state['phase']==2:
            act.roles.set_tasks(UnitTask.Reserved,troops)
            if 'before' not in state:
                state['before']=[r.name for r in await ai.client.query_building_placement(ability,[center],ignore_resources=True)]
                state['before_overlapping']=sum(overlaps_townhall_site(u.position,center,u.radius) for u in troops)
            if state['move']:
                await runtime['mission'].execute()
                state['station']=list(runtime['mission']._hold_point)
                state['overlapping']=sum(overlaps_townhall_site(u.position,center,u.radius) for u in troops)
                state['after']=[r.name for r in await ai.client.query_building_placement(ability,[center],ignore_resources=True)]
                if state['after']==['Success'] and state['overlapping']==0:
                    state.setdefault('cleared_placement',state['after'])
                    state.setdefault('cleared_overlapping',state['overlapping'])
                state['mobile']=sum(u.type_id==U.SIEGETANK for u in troops) if race=='terran' else None
            state['construction_started']=any(h.position.distance_to(center)<4 for h in ai.townhalls)
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir=Path('records/platform_audit_mechanics_20261031')/f'expansion_station_{race}')
    def advance(seconds):
        return drive(env,[{'name':'advance','arguments':{'seconds':seconds}}])
    try:
        env.reset(EpisodeConfig(race=race,enemy_race='terran',opponent='veryeasy',
            map_name='AcropolisLE',game_time_limit_seconds=120))
        for _ in range(6):
            advance(1)
            if 'before' in state:break
        assert state['phase']==2 and state['before_overlapping']>0,state
        state['move']=True
        for _ in range(5):
            advance(4)
            if state.get('cleared_placement')==['Success']:break
        destination.write_text(json.dumps(state,indent=2),encoding='utf8')
        assert state.get('cleared_placement')==['Success'] and state['cleared_overlapping']==0,state
        drive(env,[{'name':'build','arguments':{'target':name}},
                   {'name':'advance','arguments':{'seconds':10}}])
        for _ in range(4):
            if state['construction_started']:break
            advance(5)
        destination.write_text(json.dumps(state,indent=2),encoding='utf8')
        assert state['construction_started'],state
        state['finish']=True
        advance(2)
    finally:
        env.close()
