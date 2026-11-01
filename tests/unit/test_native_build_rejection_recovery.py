"""A positive placement query must not override a later native rejection."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock
import pytest

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
_ensure_runtime_paths()
from sc2.data import ActionResult, Race
from sc2.ids.ability_id import AbilityId as A
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.position import Point2
from sharpy.plans.acts import GridBuilding, Expand


def fixture():
    act=GridBuilding(U.SUPPLYDEPOT,1)
    bad,good=Point2((10,10)),Point2((14,10))
    worker=NS(tag=1,orders=[NS(ability=NS(id=A.TERRANBUILD_SUPPLYDEPOT),target=bad)],stop=MagicMock())
    act.ai=NS(time=10,state=NS(action_errors=[]),
              _game_data=NS(units={U.SUPPLYDEPOT.value:NS(creation_ability=NS(id=A.TERRANBUILD_SUPPLYDEPOT,exact_id=A.TERRANBUILD_SUPPLYDEPOT))}),
              can_place=AsyncMock(return_value=[True]),find_placement=AsyncMock(return_value=good))
    act.builder_tag=1
    act.cache=NS(by_tag=lambda _:worker)
    act.clear_worker=MagicMock()
    act._remember_build(worker,bad)
    return act,worker,bad,good


@pytest.mark.parametrize("failure", [ActionResult.CantBuildLocationInvalid,ActionResult.CouldntReachTarget,ActionResult.CantFindPlacementLocation])
def test_rejected_native_order_is_stopped_and_new_position_is_used(failure):
    act,worker,bad,good=fixture()
    act.ai.state.action_errors=[NS(unit_tag=1,ability_id=A.TERRANBUILD_SUPPLYDEPOT.value,result=failure.value)]
    assert asyncio.run(act.execute()) is False
    worker.stop.assert_called_once()
    assert act.waiting_reason=="build_location_rejected"
    assert asyncio.run(act.checked_position(bad))==good
    act.clear_worker.assert_called_once()


def test_native_query_cannot_reselect_a_recently_rejected_point():
    act,worker,bad,good=fixture()
    act.ai.bench_rejected_build_locations={(U.SUPPLYDEPOT.value,tuple(bad)):20}
    act.ai.find_placement=AsyncMock(side_effect=[bad,bad,good])
    assert asyncio.run(act.checked_position(bad))==good
    assert act.ai.find_placement.await_count==3


def test_all_rejected_alternatives_wait_without_claiming_a_build():
    act,worker,bad,good=fixture()
    act.ai.bench_rejected_build_locations={(U.SUPPLYDEPOT.value,tuple(bad)):20}
    act.ai.find_placement=AsyncMock(return_value=bad)
    assert asyncio.run(act.checked_position(bad)) is None
    assert act.waiting_reason=="build_location_rejected"
    act.ai.time=21
    assert asyncio.run(act.checked_position(bad))==bad


def test_unrelated_error_does_not_stop_a_valid_worker_order():
    act,worker,bad,good=fixture()
    act.ai.state.action_errors=[NS(unit_tag=2,ability_id=A.TERRANBUILD_SUPPLYDEPOT.value,result=ActionResult.CantBuildLocationInvalid.value)]
    assert act._observe_build_rejection() is False
    worker.stop.assert_not_called()


def test_cached_slot_failure_uses_a_legal_nearby_solver_slot_on_other_parity():
    act,worker,bad,_=fixture()
    valid=bad.offset((3,3))
    far=bad.offset((20,0))
    act.building_solver=NS(buildings2x2=[bad,valid,far])
    act.ai.can_place=AsyncMock(side_effect=[[False],[True]])
    act.ai.find_placement=AsyncMock(return_value=None)
    assert asyncio.run(act.checked_position(bad))==valid
    assert act.ai.can_place.call_args.args[1]==[valid]
    act.ai.find_placement.assert_not_awaited()


def test_other_solver_slot_cannot_overlap_a_pending_structure():
    act,worker,bad,_=fixture()
    blocked=bad.offset((3,3))
    valid=bad.offset((5,0))
    act.building_solver=NS(buildings2x2=[bad,blocked,valid])
    act._pending_build_overlap=lambda p:p==blocked
    act.ai.can_place=AsyncMock(side_effect=[[False],[True]])
    assert asyncio.run(act.checked_position(bad))==valid
    assert act.ai.can_place.call_args.args[1]==[valid]


def test_grid_recovery_can_use_a_further_slot_in_the_same_base_zone():
    act,worker,bad,_=fixture()
    valid=bad.offset((11,0))
    other_zone=bad.offset((12,0))
    class Grid:
        def __getitem__(self,point):return NS(ZoneIndex=2 if point==other_zone else 1)
    act.building_solver=NS(buildings2x2=[bad,valid,other_zone],grid=Grid())
    act.ai.can_place=AsyncMock(side_effect=[[False],[True]])
    assert asyncio.run(act.checked_position(bad))==valid
    assert act.ai.can_place.call_args.args[1]==[valid]


@pytest.mark.parametrize("race,kind,ability", [
    (Race.Terran,U.COMMANDCENTER,A.TERRANBUILD_COMMANDCENTER),
    (Race.Protoss,U.NEXUS,A.PROTOSSBUILD_NEXUS),
    (Race.Zerg,U.HATCHERY,A.ZERGBUILD_HATCHERY),
])
def test_expansion_recovers_rejected_center_without_reusing_its_bad_order(race,kind,ability):
    act=Expand(2)
    act.knowledge=NS(my_race=race)
    center,nearby=Point2((20,20)),Point2((24,20))
    worker=NS(tag=1,orders=[NS(ability=NS(id=ability),target=center)],stop=MagicMock(),build=MagicMock())
    act.ai=NS(time=10,state=NS(action_errors=[NS(unit_tag=1,ability_id=ability.value,result=ActionResult.CouldntReachTarget.value)]),
              _game_data=NS(units={kind.value:NS(creation_ability=NS(id=ability,exact_id=ability))}),
              can_place=AsyncMock(return_value=[True]),find_placement=AsyncMock(return_value=nearby))
    act.builder_tag=1
    act.cache=NS(by_tag=lambda _:worker)
    act.clear_worker=MagicMock()
    act.set_worker=MagicMock()
    act.get_worker_builder=lambda *_:worker
    act.print=lambda *_:None
    act._remember_build(worker,center)
    assert asyncio.run(act.execute()) is False
    worker.stop.assert_called_once()
    assert act.waiting_reason=="build_location_rejected"
    assert asyncio.run(act.build_expansion(NS(center_location=center))) is True
    worker.build.assert_called_once_with(kind,nearby)
    assert act.ai.find_placement.call_args.kwargs["max_distance"]==4
    act.ai.workers=[NS(orders=[NS(ability=NS(id=ability),target=nearby)])]
    assert act.expanding_in(NS(center_location=center))
