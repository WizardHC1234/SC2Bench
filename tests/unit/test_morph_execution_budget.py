"""Multiple morphs share the actual incremental resource and supply budget."""
import asyncio
import pytest
from types import SimpleNamespace as NS
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.ids.ability_id import AbilityId as A
from sc2.units import Units
from sc2bench_env.backends.sharpy.races.zerg import BenchMorph
from tests.unit.test_combat_execution import UnitStub


def scene(kind,result,ability,cocoon,count=2):
    class Source(UnitStub):
        accepted=True
        def __call__(self,ability,*args,**kwargs):
            self.commands.append(ability)
            return self.accepted
    units=[Source(i,kind,orders=[NS(ability=NS(id=A.HOLDPOSITION_HOLD))]) for i in range(1,count+1)]
    ai=NS(time=0,minerals=1000,vespene=75,supply_left=8,supply_used=20,
          bench_group0_tags={u.tag for u in units},bench_combat_tags=set(),all_enemy_units=[])
    ai.units=Units(units,ai);ai.structures=Units([],ai)
    act=BenchMorph(kind,ability,result,cocoon,count);act.ai=ai
    return act,units


def test_ravager_batch_does_not_issue_more_than_the_available_gas_can_pay():
    act,units=scene(U.ROACH,U.RAVAGER,A.MORPHTORAVAGER_RAVAGER,U.RAVAGERCOCOON)
    assert asyncio.run(act.execute()) is False
    assert sum(len(u.commands) for u in units)==1
    assert (act.ai.minerals,act.ai.vespene,act.ai.supply_left)==(975,0,7)
    assert units[1].tag in act.ai.bench_group0_tags


@pytest.mark.parametrize('free_supply',[0,-4])
def test_baneling_morph_needs_no_additional_supply_at_the_cap(free_supply):
    act,units=scene(U.ZERGLING,U.BANELING,A.MORPHZERGLINGTOBANELING_BANELING,U.BANELINGCOCOON,1)
    act.ai.supply_left=free_supply
    assert asyncio.run(act.execute()) is True
    assert len(units[0].commands)==1
    assert act.ai.supply_left==free_supply and act.ai.supply_used==20
    assert (act.ai.minerals,act.ai.vespene)==(975,50)


def test_rejected_morph_keeps_source_membership_and_does_not_charge_planning_bank():
    act,units=scene(U.ROACH,U.RAVAGER,A.MORPHTORAVAGER_RAVAGER,U.RAVAGERCOCOON,1)
    units[0].accepted=False
    assert asyncio.run(act.execute()) is False
    assert act.ai.bench_group0_tags=={1}
    assert (act.ai.minerals,act.ai.vespene,act.ai.supply_left)==(1000,75,8)


def test_native_rejection_returns_the_still_living_source_for_retry():
    act,units=scene(U.ROACH,U.RAVAGER,A.MORPHTORAVAGER_RAVAGER,U.RAVAGERCOCOON,1)
    assert asyncio.run(act.execute()) is True
    assert 1 not in act.ai.bench_group0_tags
    act.ai.time=1;act.ai.vespene=75
    assert asyncio.run(act.execute()) is True
    assert len(units[0].commands)==2


@pytest.mark.parametrize('source,result,form,ability,cocoon',[
    (U.HYDRALISK,U.LURKERMP,U.LURKERMPBURROWED,A.MORPH_LURKER,U.LURKERMPEGG),
    (U.ZERGLING,U.BANELING,U.BANELINGBURROWED,A.MORPHZERGLINGTOBANELING_BANELING,U.BANELINGCOCOON),
    (U.ROACH,U.RAVAGER,U.RAVAGERBURROWED,A.MORPHTORAVAGER_RAVAGER,U.RAVAGERCOCOON),
    (U.OVERLORD,U.OVERSEER,U.OVERSEERSIEGEMODE,A.MORPH_OVERSEER,U.OVERLORDCOCOON)])
def test_existing_alternate_forms_do_not_cause_extra_morphs(source,result,form,ability,cocoon):
    act,units=scene(source,result,ability,cocoon,4)
    act.ai.vespene=1000
    act.ai.units=Units(units+[UnitStub(10,form),UnitStub(11,form)],act.ai)
    assert asyncio.run(act.execute()) is True
    assert sum(len(u.commands) for u in units)==2


def test_transport_overlord_uses_its_catalog_cost_without_a_missing_producer_lookup():
    act,units=scene(U.OVERLORD,U.OVERLORDTRANSPORT,A.MORPH_OVERLORDTRANSPORT,U.TRANSPORTOVERLORDCOCOON,1)
    assert asyncio.run(act.execute()) is True
    assert len(units[0].commands)==1
