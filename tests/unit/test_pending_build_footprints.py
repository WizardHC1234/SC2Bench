"""Placement queries cannot see other builders' not-yet-started structures."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,MagicMock
import pytest
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.ids.ability_id import AbilityId as A
from sc2.position import Point2
from sharpy.plans.acts import GridBuilding


def setup(kind,ability,source,point=None):
    point=point or Point2((10,10))
    data=NS(id=ability,exact_id=ability,_proto=NS(is_building=True,footprint_radius=1.5))
    act=GridBuilding(kind)
    order=NS(ability=data,target=point)
    command=NS(ability=ability,target=point)
    act.ai=NS(time=1,_game_data=NS(units={kind.value:NS(footprint_radius=1.5)},
        abilities={ability.value:data}),actions=[command] if source=='queued' else [],
        workers=[NS(orders=[order])] if source=='native' else [],
        can_place=AsyncMock(return_value=[True]),find_placement=AsyncMock(return_value=Point2((6,10))))
    act.clear_worker=MagicMock()
    return act,point


@pytest.mark.parametrize('source',['queued','native'])
@pytest.mark.parametrize('kind,ability',[(U.ENGINEERINGBAY,A.TERRANBUILD_BARRACKS),
    (U.ROBOTICSFACILITY,A.PROTOSSBUILD_GATEWAY),(U.ROACHWARREN,A.ZERGBUILD_EVOLUTIONCHAMBER)])
def test_a_positive_native_query_does_not_reuse_a_pending_build_site(source,kind,ability):
    act,point=setup(kind,ability,source)
    assert asyncio.run(act.checked_position(point))==Point2((6,10))


def test_touching_footprint_edges_remain_usable():
    act,_=setup(U.ROBOTICSFACILITY,A.PROTOSSBUILD_GATEWAY,'native')
    point=Point2((13,10))
    assert asyncio.run(act.checked_position(point))==point
    act.ai.find_placement.assert_not_awaited()


def test_an_existing_walk_command_does_not_reserve_a_building_site():
    act,point=setup(U.ROBOTICSFACILITY,A.PROTOSSBUILD_GATEWAY,'native')
    act.ai.workers[0].orders[0].ability._proto.is_building=False
    assert asyncio.run(act.checked_position(point))==point


def test_native_fallback_cannot_choose_another_pending_footprint():
    act,point=setup(U.ROBOTICSFACILITY,A.PROTOSSBUILD_GATEWAY,'native')
    act.ai.find_placement=AsyncMock(side_effect=[point,point,Point2((6,10))])
    assert asyncio.run(act.checked_position(point))==Point2((6,10))


def test_a_cancelled_native_build_order_releases_the_site_without_a_cache():
    act,point=setup(U.ROBOTICSFACILITY,A.PROTOSSBUILD_GATEWAY,'native')
    assert asyncio.run(act.checked_position(point))!=point
    act.ai.workers[0].orders=[]
    assert asyncio.run(act.checked_position(point))==point


@pytest.mark.parametrize('source',['queued','native'])
def test_pending_terran_production_keeps_its_addon_footprint_clear(source):
    act,_=setup(U.ENGINEERINGBAY,A.TERRANBUILD_BARRACKS,source)
    assert asyncio.run(act.checked_position(Point2((13.5,9.5))))==Point2((6,10))
