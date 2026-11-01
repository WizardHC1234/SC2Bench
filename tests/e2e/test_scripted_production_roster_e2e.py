"""Catalog-driven production and group movement; no LLM or strategy selection.

Resources, build speed and Zerg larva are supplied only in this isolated test.
Every requested unit, morph and merge still goes through the platform tools.
"""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.catalog.registry import get_target,targets_for_action
from sc2bench_env.backends.sharpy.races import get_adapter
from sc2bench_env.interface.tools import ToolCall
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


@pytest.mark.parametrize('race',['terran','protoss','zerg'])
def test_all_catalog_units_are_produced_and_can_join_movement(monkeypatch,race):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    state=dict(race=race,setup=False,targets=[],errors=[],movement=None)
    from sharpy.plans.acts.build_location_recovery import BuildLocationRecovery
    checked_position = BuildLocationRecovery.checked_position
    async def diagnose_placement(act, point, **kwargs):
        result = await checked_position(act, point, **kwargs)
        failures=state.setdefault('placement_failures',{})
        if result is None and act.unit_type.name not in failures:
            points = list(getattr(act.building_solver, 'buildings3x3', []))
            valid = await act.ai.can_place(act.unit_type, points) if points else []
            failures[act.unit_type.name] = dict(
                unit=act.unit_type.name, selected=list(point), seconds=act.ai.time,
                candidates=[dict(point=list(p),valid=bool(ok),powered=act.ai.state.psionic_matrix.covers(p))
                            for p,ok in zip(points,valid)],
                structures=[dict(type=u.type_id.name,point=list(u.position),radius=u.radius)
                            for u in act.ai.structures])
            save()
        return result
    monkeypatch.setattr(BuildLocationRecovery,'checked_position',diagnose_placement)
    from sc2bench_env.backends.sharpy.acts import ActCombatMission
    execute_mission=ActCombatMission.execute
    async def observe_mission(act):
        result=await execute_mission(act)
        if state.get('movement_started') and act._bound:
            members=state.setdefault('mission_units',{})
            for unit in act.ai.units:
                tracked=[(unit.tag,unit.type_id.name,unit.position)]
                tracked.extend((p.tag,p.type_id.name,unit.position) for p in unit.passengers)
                for tag,type_name,point in tracked:
                    if tag not in act._tags:continue
                    row=members.setdefault(str(tag),dict(type=type_name,first=list(point),max_distance=0))
                    row['last']=list(point)
                    row['max_distance']=max(row['max_distance'],((point.x-row['first'][0])**2+(point.y-row['first'][1])**2)**.5)
        return result
    monkeypatch.setattr(ActCombatMission,'execute',observe_mission)
    adapter=get_adapter(race)
    destination=Path(f'analysis/platform_audit/scripted_roster_{race}.json')
    destination.parent.mkdir(parents=True,exist_ok=True)
    def save():destination.write_text(json.dumps(state,indent=2),encoding='utf8')
    async def collect(act):
        ai=act.ai
        state['errors'].extend(dict(seconds=ai.time,actor=e.unit_tag,ability=e.ability_id,result=e.result)
                               for e in ai.state.action_errors)
        if not state['setup']:
            await ai.client.debug_all_resources()
            await ai.client.debug_fast_build()
            state['setup']=True
        elif ai.minerals<2000 or ai.vespene<2000:
            await ai.client.debug_all_resources()
        if race=='zerg' and ai.units(U.LARVA).amount<4:
            await ai.client.debug_create_unit([[U.LARVA,12,ai.start_location,ai.player_id]])
        state['last_units']=[dict(tag=u.tag,type=u.type_id.name,point=list(u.position),
                                 home=u.tag in getattr(ai,'bench_group0_tags',set()),
                                 orders=[dict(ability=o.ability.id.name,target=str(o.target)) for o in u.orders])
                             for u in ai.units if adapter.normalize_unit_name(u.type_id.name)]
        gather=getattr(ai,'bench_home_gather',None)
        if gather is not None:
            state['home_point']=list(gather.home_point())
            state['unavailable_details']=[dict(type=u.type_id.name,tag=u.tag,
                idle=u.is_idle,ready=u.is_ready,role=str(act.roles.unit_role(u)),
                eligible=gather.eligible(u,rally_point=gather.home_point()),
                mineral_targets=[o.target for o in u.orders if isinstance(o.target,int)
                                 and o.target in ai.mineral_field.tags])
                for u in ai.units if u.tag not in getattr(ai,'bench_group0_tags',set())
                and adapter.normalize_unit_name(u.type_id.name)]
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir=f'records/scripted_roster_20261101/{race}')
    def step(calls=None,seconds=3):
        turn=env.begin_tool_turn()
        for item in (calls or [])+[{'name':'advance','arguments':{'seconds':seconds}}]:
            result=turn.call(ToolCall(item['name'],item['arguments']))
            assert result.status!='rejected', result
        obs,feedback,done,info=env.step(turn.finish())
        assert not done,info
        return obs,feedback
    def owned(name):
        snap=env.backend.snapshot()
        return sum(snap.units.get(n,0)+snap.buildings.get(n,0)
                   for n in adapter.prerequisite_count_keys(name))
    def wait_for(predicate,description):
        for _ in range(30):
            if predicate():return
            step()
        raise AssertionError(f'timed out: {description}; queue={env.task_manager.production_priority_summary()}')
    def train(name,count):
        before=set(env.task_manager.demands)
        _,feedback=step([{'name':'train','arguments':{'target':name,'count':count}}])
        assert feedback.receipts[0].result=='accepted',feedback
        created=[d for k,d in env.task_manager.demands.items() if k not in before and d.action=='train']
        assert len(created)==1,created
        demand=created[0]
        wait_for(lambda:demand.produced>=count or bool(demand.failure_reason),name)
        assert not demand.failure_reason,demand.failure_reason
        return demand.produced
    def ensure(name,amount=1,stack=()):
        if owned(name)>=amount:return
        spec=get_target(name,race=race)
        if spec is None:return  # Larva and other native producers have no action target.
        assert name not in stack,stack+(name,)
        stack=stack+(name,)
        for prerequisite in spec.prerequisites:
            ensure(prerequisite,2 if spec.mechanism=='merge' else 1,stack)
        if spec.morph_from:ensure(spec.morph_from,1,stack)
        if spec.produced_at and get_target(spec.produced_at,race=race):
            ensure(spec.produced_at,2 if spec.mechanism=='merge' else 1,stack)
        if spec.action=='train':
            train(name,amount-owned(name))
        elif spec.action=='morph_townhall':
            _,feedback=step([{'name':'morph_townhall','arguments':{'target':'townhall_0','to':name}}])
            assert feedback.receipts[0].result=='accepted',feedback
            wait_for(lambda:owned(name)>=amount,name)
        elif spec.action=='build':
            if race=='zerg' and spec.mechanism!='structure_morph':ensure('drone',1,stack)
            _,feedback=step([{'name':'build','arguments':{'target':name}}
                             for _ in range(max(1,amount-owned(name)))])
            assert feedback.receipts[0].result=='accepted',feedback
            wait_for(lambda:owned(name)>=amount,name)
        elif spec.action=='research':
            step([{'name':'research','arguments':{'target':name}}])
            wait_for(lambda:env.latest_observation.research.get(name)=='completed',name)
    try:
        env.reset(EpisodeConfig(race=race,enemy_race='terran',opponent='veryeasy',
                               map_name='AcropolisLE',game_time_limit_seconds=1200))
        step()
        supply={'terran':'supply_depot','protoss':'pylon','zerg':'overlord'}[race]
        if race=='zerg':train(supply,8)
        else:ensure(supply,8)
        # A native path/placement rejection is recoverable, but only count it
        # as recovered after every requested supply structure really exists.
        from sc2.data import ActionResult
        from sc2.ids.ability_id import AbilityId
        setup_errors=list(state['errors'])
        building_ability={'terran':AbilityId.TERRANBUILD_SUPPLYDEPOT,
                          'protoss':AbilityId.PROTOSSBUILD_PYLON}.get(race)
        for error in setup_errors:
            assert building_ability is not None and error['ability']==building_ability.value,error
            result=ActionResult(error['result']).name
            assert result in {'CouldntReachTarget','CantFindPlacementLocation'} or result.startswith('CantBuild'),error
        state['supply_setup']=dict(target=supply,actual=owned(supply),
                                  recovered_native_errors=setup_errors)
        if race!='zerg':assert owned(supply)==8,state['supply_setup']
        specs=[s for s in targets_for_action('train',race=race) if s.executable]
        for spec in specs:
            row=dict(target=spec.name,mechanism=spec.mechanism,status='running')
            state['targets'].append(row)
            try:
                for prerequisite in spec.prerequisites:
                    ensure(prerequisite,2 if spec.mechanism=='merge' else 1)
                if spec.morph_from:ensure(spec.morph_from)
                if spec.produced_at and get_target(spec.produced_at,race=race):
                    ensure(spec.produced_at,2 if spec.mechanism=='merge' else 1)
                row['produced']=train(spec.name,1)
                row['status']='passed'
            except AssertionError as error:
                row.update(status='failed',reason=str(error))
                save()
                raise
            save()
        if all(r['status']=='passed' for r in state['targets']):
            for spec in specs:
                if spec.dispatchable:ensure(spec.name)
            step()
            units={s.name:1 for s in specs if s.dispatchable and owned(s.name)>0}
            wait_for(lambda:all(env.latest_observation.own_forces.free.get(n,0)>=c
                                for n,c in units.items()),'all units available for dispatch')
            # The public Queen contract permits defense missions only.
            defense_group=None
            if 'queen' in units:
                _,feedback=step([{'name':'dispatch_group','arguments':{'source':'group_0','order':'defend','target':'zone_0','units':{'queen':units.pop('queen')}}}])
                assert feedback.receipts[0].result=='accepted',feedback
                defense_group=feedback.receipts[0].group
            _,feedback=step([{'name':'dispatch_group','arguments':{'source':'group_0','order':'defend','target':'zone_0','units':units}}])
            assert feedback.receipts[0].result=='accepted',feedback
            group=feedback.receipts[0].group
            state['movement_started']=True
            calls=[{'name':'attack_group','arguments':{'group':group,'target':'zone_1'}}]
            if defense_group:
                calls.append({'name':'defend_group','arguments':{'group':defense_group,'target':'zone_1'}})
            step(calls,seconds=12)
            expected=sum(units.values())+(1 if defense_group else 0)
            wait_for(lambda:len(state.get('mission_units',{}))>=expected
                     and all(u['max_distance']>2 for u in state['mission_units'].values()),
                     'actual movement of each mission member')
            state['movement']=dict(group=group,units=units,defense_only_group=defense_group,
                                   tracked_members=expected,completed=True)
        save()
        assert all(r['status']=='passed' for r in state['targets']),state['targets']
        assert not state['errors'][len(setup_errors):],state['errors'][len(setup_errors):]
        assert not env.task_manager.production_priority_summary()
    finally:
        state['record_directory']=str(env.record_path)
        save()
        env.close()
