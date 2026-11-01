"""Game-facing contracts: explicit spending, observable capacity and valid producers."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest

from sc2bench_env.runtime.query_tools import query_production_state
from sc2bench_env.adapters.llm.tool_results import render_tool_result
from sc2bench_env.backends.sharpy.races.protoss import ProtossAdapter, ProtossGridBuilding
from sc2bench_env.runtime.task import Demand
from sc2bench_env.backends.sharpy.defense_placement import DefensiveGridBuilding
from sc2bench_env.backends.sharpy.macro import ActOngoingMacroTasks
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.position import Point2
from sharpy.plans.acts import GridBuilding
from sharpy.managers.core.grids import ZoneArea


def test_scv_query_keeps_actual_upgraded_producers_and_exposes_observed_work():
    result=query_production_state({
        "building":{"orbital_command":{"completed":1},"factory":{"completed":1}},
        "production":[{"facility":"command_center","ready_grounded":1,"free_slots":0,
                       "capacity":1,"queue_capacity":5,"free_queue_positions":4},
                      {"facility":"factory","ready_grounded":1,"free_slots":1}],
        "training":{"scv":{"in_production":1},"siege_tank":{"in_production":1}},
        "research":{"stimpack":"in_progress"}}, targets=["scv"])
    assert set(result["infrastructure"])=={"orbital_command"}
    assert result["production_capacity"]==[{"facility":"command_center","ready_grounded":1,"free_slots":0,"capacity":1}]
    assert result["observed_work"]["training"]=={"scv":1}
    text=render_tool_result("query_production_state",result)
    assert "free slots 0/1" in text and "scv x1" in text and "orbital_command" in text
    assert "queue_capacity" not in text and "siege_tank" not in text


def test_research_query_keeps_attached_host_capacity_and_research_in_progress():
    result=query_production_state({
        "building":{"barracks":{"completed":2},"barracks_techlab":{"completed":1}},
        "production":[{"facility":"barracks","ready_grounded":2,"techlab_hosts":1,"capacity":2,"free_slots":1}],
        "research":{"stimpack":"in_progress","combat_shield":"completed"}},targets=["stimpack"])
    assert "barracks" in result["infrastructure"]
    assert len(result["production_capacity"])==1
    assert result["observed_work"]["research"]==["stimpack"]


def test_unit_morph_query_keeps_its_sources_without_implying_larva_production():
    result=query_production_state({"production":[
        {"facility":"larva","ready_grounded":3,"free_slots":3},
        {"facility":"baneling_source","ready_grounded":6,"free_slots":4}],
        "building":{"hatchery":{"completed":1}}},targets=["baneling"],race="zerg")
    assert [row["facility"] for row in result["production_capacity"]]==["baneling_source"]


def test_townhall_morph_query_accepts_form_name_and_retains_actual_source():
    work={"action":"morph_townhall","target":"townhall_0","to":"lair","remaining":1}
    result=query_production_state({"production_priority":[work],"building":{"hatchery":{"completed":1},"lair":{"under_construction":1}}},
                                  actions=["morph_townhall"],targets=["lair"],race="zerg")
    assert result["persistent_work"]==[work]
    assert "hatchery" in result["infrastructure"]
    assert result["observed_work"]["construction"]=={"lair":1}


def test_missing_production_metadata_remains_unknown():
    result=query_production_state({"building":{}})
    assert result["production_capacity"] is None
    assert result["observed_work"]["training"] is None
    assert "Production capacity:\n- unknown" in render_tool_result("query_production_state",result)


@pytest.mark.parametrize("vision", ["visible","fogged"])
def test_center_vision_does_not_hide_history_or_turn_enemy_age_into_fog_duration(vision):
    from sc2bench_env.interface.observation_text import brief_zone_lines
    text="\n".join(brief_zone_lines([{
        "zone_id":"zone_1","zone_role":"own_natural","known_owner":"unconfirmed",
        "vision_state":vision,"own_contents":{},"visible_enemy_contents":{},
        "last_seen_enemy_contents":{"units":{"siege_tank":2},"buildings":{}},
        "enemy_information_age_seconds":20.5,
    }]))
    assert "center "+vision in text
    assert "last seen units: siege_tank 2" in text
    assert "oldest sighting 20s ago" in text
    assert "fogged for" not in text


@pytest.mark.parametrize("target", ["photon_cannon","shield_battery"])
def test_defensive_construction_never_creates_an_unrequested_pylon(monkeypatch,target):
    helper=NS(execute=AsyncMock(return_value=False))
    async def parent_start(self,knowledge):
        self.make_pylon=helper
    monkeypatch.setattr(GridBuilding,"start",parent_start)
    act=ProtossAdapter().create_act(Demand(action="build",target=target),to_count=1)
    assert isinstance(act,DefensiveGridBuilding)
    asyncio.run(act.start(None))
    act.knowledge=NS(my_race=Race.Protoss)
    act.get_count=lambda *a,**k:0
    act.pending_build=lambda _:0
    act.cache=NS(own=lambda _:NS(not_ready=NS(amount=0)))
    act.position_protoss=lambda _:None
    act.print=lambda *_:None
    assert asyncio.run(act.execute()) is False
    helper.execute.assert_not_awaited()


def test_unpowered_building_does_not_block_an_explicit_pylon_request():
    class Structures(list):
        def __call__(self,_):return NS(ready=[])
        def closer_than(self,*_):return False
    class Grid:
        def __getitem__(self,_):return NS(ZoneIndex=ZoneArea.OwnMainZone)
    ai=NS(structures=Structures(),units=lambda _:NS(ready=[object()]),
          state=NS(psionic_matrix=NS(covers=lambda _:False)),
          minerals=100,vespene=0,supply_cap=30,supply_used=15,already_pending=lambda _:0)
    blocked=ProtossGridBuilding(U.GATEWAY,1)
    blocked.ai=ai
    blocked.building_solver=NS(grid=Grid(),buildings2x2=[Point2((0,0))],buildings3x3=[Point2((5,0))],wall2x2=[],wall3x3=[])
    blocked.cache=NS(own=lambda _:NS(not_ready=[]))
    blocked.execute=AsyncMock(return_value=False)
    pylon=NS(execute=AsyncMock(return_value=False))
    first=dict(action="build",target="gateway",to_count=1,order_index=1,_act=blocked,_started=True)
    second=dict(action="build",target="pylon",to_count=1,order_index=2,_act=pylon,_started=True)
    macro=ActOngoingMacroTasks([first,second],race="protoss")
    macro.ai=ai
    macro._count_ready=lambda _:1
    macro._ready_progress=lambda _:0
    asyncio.run(macro.execute())
    assert first["_waiting_for"]=="no_powered_build_location"
    blocked.execute.assert_not_awaited()
    pylon.execute.assert_awaited_once()


def test_supply_blocked_units_do_not_withhold_a_funded_supply_building():
    from tests.unit.test_execution_blockers import macro
    m=macro()
    m.ai.minerals,m.ai.vespene=100,0
    m.ai.supply_cap=m.ai.supply_used=15
    m._count_ready=lambda _:1
    m._ready_progress=lambda _:0
    marine=NS(execute=AsyncMock(return_value=False))
    depot=NS(execute=AsyncMock(return_value=False))
    first=dict(action="train",target="marine",order_index=1,_act=marine,_started=True)
    second=dict(action="build",target="supply_depot",order_index=2,_act=depot,_started=True)
    m.active_tasks=[first,second]
    asyncio.run(m.execute())
    assert first["_waiting_for"]=="supply"
    marine.execute.assert_not_awaited()
    depot.execute.assert_awaited_once()


def test_resource_waiting_unit_does_not_reserve_unpaid_population():
    from tests.unit.test_execution_blockers import macro
    m=macro()
    m.ai.minerals,m.ai.vespene=300,0
    m.ai.supply_cap,m.ai.supply_used=18,15
    m._count_ready=lambda _:1
    m._ready_progress=lambda _:0
    tank=NS(execute=AsyncMock(return_value=False))
    marine=NS(execute=AsyncMock(return_value=False))
    first=dict(action="train",target="siege_tank",order_index=1,_act=tank,_started=True)
    second=dict(action="train",target="marine",order_index=2,_act=marine,_started=True)
    m.active_tasks=[first,second]
    asyncio.run(m.execute())
    assert first["_waiting_for"]=="resources"
    tank.execute.assert_not_awaited()
    marine.execute.assert_awaited_once()
