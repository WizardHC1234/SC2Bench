"""A busy research facility cannot hold the bank against runnable production."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.units import Units
from sc2bench_env.backends.sharpy.macro import ActOngoingMacroTasks
from sc2bench_env.backends.sharpy.races.base import research_host_blocker


@pytest.mark.parametrize('race,research,kind,worker,hall',[
    ('terran','infantry_armor_1',U.ENGINEERINGBAY,'scv',U.COMMANDCENTER),
    ('protoss','ground_armor_1',U.FORGE,'probe',U.NEXUS),
    ('zerg','ground_carapace_1',U.EVOLUTIONCHAMBER,'drone',U.HATCHERY)])
def test_busy_research_leaves_budget_for_a_later_worker(race,research,kind,worker,hall):
    research_act=NS(from_buildings={kind},execute=AsyncMock(return_value=False))
    worker_act=NS(execute=AsyncMock(return_value=False))
    first=dict(action='research',target=research,order_index=1,_act=research_act,_started=True)
    second=dict(action='train',target=worker,order_index=2,_act=worker_act,_started=True)
    macro=ActOngoingMacroTasks([first,second],race=race)
    parents=[NS(tag=1,type_id=kind,is_ready=True,is_powered=True,is_flying=False,orders=[object()],add_on_tag=0),
             NS(tag=2,type_id=hall,is_ready=True,is_flying=False,orders=[],add_on_tag=0)]
    ai=NS(minerals=50,vespene=0,supply_cap=30,supply_used=8,already_pending=lambda _:0)
    ai.structures=Units(parents,ai);ai.units=Units([NS(type_id=U.LARVA)],ai)
    macro.ai=ai;macro._count_ready=lambda _:1;macro._ready_progress=lambda _:0
    asyncio.run(macro.execute())
    assert first['_waiting_for']=='producer_busy'
    research_act.execute.assert_not_awaited()
    worker_act.execute.assert_awaited_once()


@pytest.mark.parametrize('orders,expected',[([object()],'producer_busy'),([],None),(None,None)])
def test_one_ready_research_host_and_unknown_orders(orders,expected):
    host=NS(type_id=U.FORGE,is_ready=True,is_flying=False,is_powered=True,orders=orders)
    task={'action':'research','_act':NS(from_buildings={U.FORGE})}
    assert research_host_blocker(NS(structures=[host]),task,race='protoss')==expected


def test_idle_second_facility_allows_research_and_unpowered_does_not():
    busy=NS(type_id=U.FORGE,is_ready=True,is_flying=False,is_powered=True,orders=[object()])
    idle=NS(type_id=U.FORGE,is_ready=True,is_flying=False,is_powered=True,orders=[])
    task={'action':'research','_act':NS(from_buildings={U.FORGE})}
    ai=NS(structures=[busy,idle])
    assert research_host_blocker(ai,task,race='protoss') is None
    idle.is_powered=False
    assert research_host_blocker(ai,task,race='protoss')=='producer_busy'
    busy.is_powered=False
    assert research_host_blocker(ai,task,race='protoss')=='unpowered'


def test_unattached_techlab_cannot_hold_the_research_budget():
    lab=NS(tag=2,type_id=U.BARRACKSTECHLAB,is_ready=True,is_flying=False,orders=[])
    host=NS(tag=1,type_id=U.BARRACKS,is_ready=True,is_flying=True,add_on_tag=2)
    task={'action':'research','_act':NS(from_buildings={U.BARRACKSTECHLAB})}
    ai=NS(structures=[host,lab])
    assert research_host_blocker(ai,task,race='terran')=='producer_techlab_unavailable'
    host.is_flying=False
    assert research_host_blocker(ai,task,race='terran') is None
