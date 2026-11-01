"""A distant zone threat cannot steal an explicitly casting Queen."""
import asyncio
from types import SimpleNamespace as NS
import pytest
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.ids.ability_id import AbilityId as A
from sharpy.managers.core.roles import UnitTask as R
from sharpy.plans.tactics.zone_defense import PlanZoneDefense
from sc2bench_env.backends.sharpy.defense import PlanZoneDefenseSafe
from tests.unit.test_combat_execution import UnitStub


@pytest.mark.parametrize('action',['inject_larva','spawn_creep_tumor'])
@pytest.mark.parametrize('distance,ally_position,enemy_range,protected',[
    (20,None,5,True), (8,None,5,False), (15,5,5,False), (15,None,16,False)])
def test_pending_caster_preserves_work_but_yields_to_local_combat(monkeypatch,action,distance,ally_position,enemy_range,protected):
    queen=UnitStub(1,U.QUEEN,sight_range=9)
    enemy=UnitStub(2,U.MARINE,position=(distance,0))
    units=[queen]
    if ally_position is not None:units.append(UnitStub(3,U.HYDRALISK,position=(ally_position,0),sight_range=11))
    act=PlanZoneDefenseSafe();role={1:R.Idle};seen=[]
    cast={'caster':1,'ability':A.EFFECT_INJECTLARVA}
    act.ai=NS(units=units,all_enemy_units=[enemy],actions=[],_macro_tasks=[dict(action=action,_act=NS(_pending_cast=cast))])
    act.roles=NS(unit_role=lambda u:role[u.tag],set_task=lambda r,u:role.__setitem__(u.tag,r))
    act.unit_values=NS(real_range=lambda attacker,target:enemy_range if attacker is enemy else 5)
    async def defense(_):
        seen.append(role[1])
        if role[1]!=R.Reserved:
            role[1]=R.Defending
            act.ai.actions.append(NS(unit=queen,ability=A.ATTACK))
        return True
    monkeypatch.setattr(PlanZoneDefense,'execute',defense)
    assert asyncio.run(act.execute()) is True
    assert seen==[R.Reserved if protected else R.Idle]
    assert role[1]==(R.Idle if protected else R.Defending)


def test_temporary_reservation_is_released_if_defense_raises(monkeypatch):
    queen=UnitStub(1,U.QUEEN,sight_range=9);role={1:R.Moving}
    act=PlanZoneDefenseSafe()
    act.ai=NS(units=[queen],all_enemy_units=[],_macro_tasks=[dict(action='inject_larva',_act=NS(_pending_cast={'caster':1}))])
    act.roles=NS(unit_role=lambda u:role[u.tag],set_task=lambda r,u:role.__setitem__(u.tag,r))
    act.unit_values=NS(real_range=lambda *_:0)
    async def defense(_):raise RuntimeError('test')
    monkeypatch.setattr(PlanZoneDefense,'execute',defense)
    with pytest.raises(RuntimeError,match='test'):asyncio.run(act.execute())
    assert role[1]==R.Moving
