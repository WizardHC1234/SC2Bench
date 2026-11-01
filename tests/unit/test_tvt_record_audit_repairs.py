"""Regressions from the TvT v4.1 record audit: report current execution facts."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
_ensure_runtime_paths()
from sc2.data import Race
from sc2.position import Point2
from sc2.ids.unit_typeid import UnitTypeId as U
from sharpy.plans.acts import Expand
from sharpy.plans.acts.grid_building import WorkerStuckStatus
from sc2bench_env.backends.sharpy.state_reader import read_snapshot
from sc2bench_env.runtime.task import DemandState
from sc2bench_env.runtime.task_manager import TaskManager, DemandUpdate
from tests.helpers.drive import submit_calls
from tests.unit.test_execution_blockers import macro
from tests.unit.test_zone_threat import UnitList


def expansion(*, safe=True, eligible=True):
    zone=NS(center_location=Point2((20,0)), should_expand_here=eligible, safe_expand_here=safe)
    act=Expand(2)
    act.ai=NS(townhalls=[NS(is_ready=True,type_id=U.COMMANDCENTER,orders=[])])
    act.knowledge=NS(my_race=Race.Terran)
    act.zone_manager=NS(our_zones_with_minerals=[object()], expansion_zones=[zone])
    act.pending_build=lambda _:0
    act.expanding_in=lambda _:False
    return act,zone


@pytest.mark.parametrize("safe,eligible,reason", [
    (False,True,"expansion_unsafe"), (True,False,"no_available_expansion"),
])
def test_blocked_expansion_reports_site_gate_and_does_not_starve_later_work(safe,eligible,reason):
    m=macro()
    m.ai.minerals=50
    m.ai.workers=NS(exists=True)
    m.ai.townhalls=[NS(is_ready=True,type_id=U.COMMANDCENTER,orders=[])]
    m._count_ready=lambda _:1
    m._ready_progress=lambda _:0
    act,zone=expansion(safe=safe,eligible=eligible)
    act.ai=m.ai
    act.execute=AsyncMock(return_value=False)
    first=dict(action="build",target="command_center",order_index=1,to_count=2,_act=act,_started=True)
    following=NS(execute=AsyncMock(return_value=False))
    second=dict(action="train",target="marine",order_index=2,_act=following,_started=True)
    m.active_tasks=[first,second]
    asyncio.run(m.execute())
    assert first["_waiting_for"]==reason
    act.execute.assert_not_awaited()
    following.execute.assert_awaited_once()
    assert m.active_tasks==[first,second]
    zone.safe_expand_here=zone.should_expand_here=True
    m.ai.minerals=500
    asyncio.run(m.execute())
    assert "_waiting_for" not in first
    act.execute.assert_awaited_once()


def test_expansion_with_native_worker_order_is_not_reported_as_blocked_site():
    act,_=expansion(safe=False)
    act.pending_build=lambda _:1
    assert act.execution_blocker() is None


@pytest.mark.parametrize("race,kind", [(Race.Terran,U.COMMANDCENTER), (Race.Protoss,U.NEXUS), (Race.Zerg,U.HATCHERY)])
@pytest.mark.parametrize("allowed", [False,True])
def test_townhall_build_checks_native_placement_before_issuing_order(allowed,race,kind):
    act,zone=expansion()
    act.knowledge.my_race=race
    act.ai=NS(can_place=AsyncMock(return_value=[allowed]),find_placement=AsyncMock(return_value=None))
    worker=MagicMock()
    act.get_worker_builder=lambda *_:worker
    act.set_worker=MagicMock()
    act.print=lambda *_:None
    assert asyncio.run(act.build_expansion(zone)) is allowed
    if allowed:
        worker.build.assert_called_once_with(kind,zone.center_location)
        act.set_worker.assert_called_once_with(worker)
    else:
        worker.build.assert_not_called()
        assert act.waiting_reason=="no_build_location"


def test_expansion_recognizes_native_point2_worker_order():
    act,zone=expansion()
    ability=object()
    act.ai=NS(workers=[NS(orders=[NS(ability=NS(id=ability),target=zone.center_location)])],
              _game_data=NS(units={U.COMMANDCENTER.value:NS(creation_ability=NS(id=ability))}))
    assert Expand.expanding_in(act,zone)
    act.ai.workers[0].orders[0].target=Point2((100,100))
    assert not Expand.expanding_in(act,zone)


def test_expansion_recognizes_protobuf_target_near_recovered_site():
    from s2clientprotocol.common_pb2 import Point2D
    act,zone=expansion()
    ability=object()
    act.ai=NS(workers=[NS(orders=[NS(ability=NS(id=ability),target=Point2D(x=23,y=0))])],
              _game_data=NS(units={U.COMMANDCENTER.value:NS(creation_ability=NS(id=ability))}))
    assert Expand.expanding_in(act,zone)


def test_stationary_builder_is_detected_after_late_first_call_and_resets_after_gap():
    status=WorkerStuckStatus()
    worker=NS(tag=1,position=Point2((0,0)))
    target=Point2((20,0))
    assert not status.need_new_worker(worker,50,target,100)
    assert not status.need_new_worker(worker,50.5,target,101)
    assert status.need_new_worker(worker,52,target,102)
    assert not status.need_new_worker(worker,60,target,110)
    assert not status.need_new_worker(worker,60.5,target,111)
    assert status.need_new_worker(worker,62,target,112)


def test_moving_builder_is_not_replaced_as_stuck():
    status=WorkerStuckStatus()
    worker=NS(tag=1,position=Point2((0,0)))
    target=Point2((20,0))
    assert not status.need_new_worker(worker,50,target,100)
    worker.position=Point2((1,0))
    assert not status.need_new_worker(worker,52,target,101)
    worker.position=Point2((2,0))
    assert not status.need_new_worker(worker,54,target,102)


def test_disappeared_worker_order_returns_unstarted_build_to_cancellable_state():
    manager=TaskManager()
    submit_calls(manager,[{"name":"build","arguments":{"target":"supply_depot"}},
                          {"name":"advance","arguments":{"seconds":5}}],game_time=0)
    demand=manager.active_demands()[0]
    manager.apply_updates([DemandUpdate(demand_id=demand.demand_id,action="build",target="supply_depot",
                                        state=DemandState.WORKER_EN_ROUTE)],game_time=1)
    assert demand.state==DemandState.WORKER_EN_ROUTE
    manager.apply_updates([DemandUpdate(demand_id=demand.demand_id,action="build",target="supply_depot",
                                        state=DemandState.WAITING_TO_START,
                                        waiting_for="builder_unavailable:scv")],game_time=2)
    assert demand.state==DemandState.WAITING_TO_START
    assert demand.is_cancellable and demand.waiting_for=="builder_unavailable:scv"


def test_home_group_reports_executor_base_even_when_station_is_nearer_another_zone():
    main=NS(center_location=Point2((0,0)),our_units=[],known_enemy_units=[])
    neighbor=NS(center_location=Point2((10,0)),our_units=[],known_enemy_units=[])
    ai=NS(structures=UnitList(),units=UnitList(),townhalls=UnitList(),gas_buildings=UnitList(),
          zone_manager=NS(expansion_zones=[main,neighbor]),all_enemy_units=[],
          bench_group0_home_point=Point2((9,0)),bench_group0_home_zone=main)
    snapshot,*_=read_snapshot(ai,NS(normalize_unit_name=lambda _:None))
    assert snapshot.info["group0_zone_id"]=="zone_0"
