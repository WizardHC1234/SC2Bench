"""Native patient exclusivity check in an isolated fixture, not a model game."""
import json
import os
from pathlib import Path
import pytest
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.e2e.test_base_abilities_e2e import _install

pytestmark=pytest.mark.skipif(os.environ.get('SC2BENCH_E2E')!='1',reason='Requires SC2')


def test_two_medivacs_cannot_heal_the_same_patient_simultaneously(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2.ids.ability_id import AbilityId as A
    from sc2.data import ActionResult
    from sharpy.managers.core.roles import UnitTask
    from s2clientprotocol import sc2api_pb2 as api,raw_pb2 as raw
    from analysis.platform_audit.probe_interface import InterfaceProbe
    from sc2bench_env.backends.sharpy.micro import MicroMedivacsSupport
    from sharpy.combat import Action,MoveType,NoAction
    from sc2.units import Units
    InterfaceProbe().install()
    state={'phase':0,'finish':False}
    async def collect(act):
        ai=act.ai
        if state['finish'] and ai.structures:
            await ai.client.debug_kill_unit([u.tag for u in ai.structures]);state['finish']=False
            return
        if state['phase']==0:
            point=ai.start_location.offset((7,0))
            await ai.client.debug_create_unit([[U.MEDIVAC,2,point,ai.player_id],
                                              [U.MARINE,2,point,ai.player_id]])
            state['phase']=1;return
        med=sorted(ai.units(U.MEDIVAC),key=lambda u:u.tag)
        patients=sorted(ai.units(U.MARINE),key=lambda u:u.tag)
        if len(med)<2 or len(patients)<2:return
        ai.bench_combat_tags=set(getattr(ai,'bench_combat_tags',()))|{u.tag for u in med+patients}
        act.knowledge.roles.set_tasks(UnitTask.Reserved,ai.units.of_type({U.MEDIVAC,U.MARINE}))
        if state['phase']==1:
            await ai.client.debug_set_unit_value([u.tag for u in med],1,100)
            await ai.client.debug_set_unit_value([u.tag for u in patients],2,4)
            state['phase']=2
        elif state['phase']==2:
            med[0](A.MEDIVACHEAL_HEAL,patients[0])
            med[1].move(patients[1].position.offset((0,3)))
            state['phase']=3
        elif state['phase']==3 and med[0].orders:
            state['first_healer_orders']=[o.ability.id.name for o in med[0].orders]
            response=await ai.client._execute(action=api.RequestAction(actions=[api.Action(
                action_raw=raw.ActionRaw(unit_command=raw.ActionRawUnitCommand(
                    ability_id=A.MEDIVACHEAL_HEAL.value,unit_tags=[med[1].tag],target_unit_tag=patients[0].tag)))]))
            state.update(phase=4,second_result=[ActionResult(r).name for r in response.action.result])
            handler=MicroMedivacsSupport();handler.ai=ai;handler.move_type=MoveType.Assault
            handler.enemies_near_by=Units([],ai)
            handler.group=type('Group',(),{'ground_units':Units(patients,ai)})()
            proposal=handler.unit_solve_combat(med[1],Action(patients[1].position,False))
            state['micro_action']=type(proposal).__name__
            state['micro_chose_other_patient']=(getattr(proposal.target,'tag',None)==patients[1].tag
                or isinstance(proposal,NoAction) and handler._healing_target(med[1])==patients[1].tag)
            proposal.to_commmand(med[1])
        elif state['phase']==4 and any(o.target==patients[1].tag for o in med[1].orders):
            state.update(phase=5,second_patient_health=patients[1].health)
    _install(monkeypatch,collect)
    env=Environment('sharpy',record_dir=Path('records/platform_audit_mechanics_20261031/medivac_patient'))
    try:
        env.reset(EpisodeConfig(opponent='veryeasy',map_name='KairosJunctionLE',game_time_limit_seconds=90))
        for _ in range(12):
            _,_,done,_=drive(env,[{'name':'advance','arguments':{'seconds':1}}])
            assert not done
            if state['phase']==5:break
        Path('analysis/platform_audit/medivac_patient_native.json').write_text(json.dumps(state,indent=2),encoding='utf8')
        assert state.get('second_result')==['AlreadyTargeted'],state
        assert state.get('phase')==5 and state.get('micro_chose_other_patient'),state
        state['finish']=True
        _,_,done,_=drive(env,[{'name':'advance','arguments':{'seconds':2}}]);assert done
    finally:env.close()
