"""Native comparison of exact and remapped attack commands on Interceptors."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


def test_native_interceptor_attack_commands(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2.ids.ability_id import AbilityId as A
    from sc2.data import ActionResult
    from sharpy.managers.core.roles import UnitTask
    from s2clientprotocol import sc2api_pb2 as s, raw_pb2 as raw
    state={'spawned':False,'finish':False,'samples':[]}
    async def collect(act):
        ai=act.ai
        if 'old_unload_tiles' not in state:
            from sc2.position import Point2
            state['old_unload_tiles']=[dict(point=list(p),walkable=bool(ai.in_pathing_grid(p)))
                for p in [Point2((97.67578,128.01928)),Point2((96.23901,128.47900)),Point2((98.24951,126.56665))]]
        if state['finish']:
            await ai.client.debug_kill_unit([b.tag for b in ai.structures]);state['finish']=False;return
        if not state['spawned']:
            center=ai.start_location.towards(ai.game_info.map_center,10)
            await ai.client.debug_create_unit([
                [U.MARINE,2,center,ai.player_id],
                [U.CARRIER,1,center.offset((4,0)),3-ai.player_id],
                [U.LOCUSTMP,1,center.offset((3,-1)),3-ai.player_id],
                [U.INTERCEPTOR,4,center.offset((3,1)),3-ai.player_id]])
            state['spawned']=True;return
        marines=ai.units(U.MARINE)
        for unit in marines:
            act.roles.set_task(UnitTask.Reserved,unit)
            await ai.client.debug_set_unit_value([unit.tag],2,1000)
        if not marines:return
        enemies=[next((u for u in ai.enemy_units if u.type_id==kind and u.is_visible),None)
                 for kind in (U.CARRIER,U.INTERCEPTOR,U.LOCUSTMP)]
        if len(state['samples'])>=54:return
        for enemy in [u for u in enemies if u is not None]:
            for ability,point_target in ((A.ATTACK.value,False),(A.ATTACK_ATTACK.value,False),(A.ATTACK.value,True)):
                command=raw.ActionRawUnitCommand(ability_id=ability,unit_tags=[marines.first.tag])
                via_fallback=False
                if point_target:
                    point=enemy.position
                    if (U.MARINE.value,enemy.tag) in getattr(ai,'_bench_unit_attack_rejections',set()):
                        from sharpy.combat import Action
                        from sharpy.combat.group_combat_manager import GroupCombatManager
                        combat=GroupCombatManager();combat.ai=ai;combat.unit_values=act.unit_values
                        recovered=combat.legal_attack_action(marines.first,Action(enemy,True),point)
                        assert recovered.is_attack and recovered.target==point
                        point=recovered.target;via_fallback=True
                    command.target_world_space_pos.CopyFrom(point.as_Point2D)
                else:
                    command.target_unit_tag=enemy.tag
                result=await ai.client._execute(action=s.RequestAction(actions=[s.Action(action_raw=raw.ActionRaw(unit_command=command))]))
                state['samples'].append(dict(time=float(ai.time),type=enemy.type_id.name,ability=ability,point_target=point_target,via_fallback=via_fallback,
                    target=enemy.tag,health=float(enemy.health),cloak=int(enemy._proto.cloak),display=int(enemy._proto.display_type),
                    result=[ActionResult(v).name for v in result.action.result]))
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir='records/platform_audit_contract_20261031/interceptor_query')
    try:
        env.reset(EpisodeConfig(race='terran',enemy_race='protoss',opponent='veryeasy',map_name='TritonLE',game_time_limit_seconds=60))
        for _ in range(15):drive(env,[{'name':'advance','arguments':{'seconds':1}}])
        Path('analysis/platform_audit/interceptor_attack_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert any(r['type']=='INTERCEPTOR' for r in state['samples']),state
        assert any(r['type']=='CARRIER' for r in state['samples']),state
        assert any(r['type']=='INTERCEPTOR' and r['point_target'] and r['result']==['Success']
                   for r in state['samples']),state
        assert any(r['type']=='LOCUSTMP' and r['point_target'] and r['result']==['Success']
                   for r in state['samples']),state
        assert any(r['via_fallback'] and r['result']==['Success'] for r in state['samples']),state
        state['finish']=True;drive(env,[{'name':'advance','arguments':{'seconds':2}}])
    finally:
        env.close()
