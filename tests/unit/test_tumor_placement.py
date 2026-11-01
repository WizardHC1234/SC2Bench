"""Creep coverage alone does not establish a legal tumor placement."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from sc2.position import Point2
from sc2.ids.ability_id import AbilityId as A
from sc2bench_env.backends.sharpy.acts import ActSpawnCreepTumor


def scene(results):
    act=ActSpawnCreepTumor()
    allowed={tuple(p) for p in results if p is not None}
    async def query(**kwargs):
        return NS(query=NS(placements=[NS(result=1 if (p.target_pos.x,p.target_pos.y) in allowed else 41)
            for p in kwargs['query'].placements]))
    act.ai=NS(time=0,enemy_start_locations=[Point2((30,0))],townhalls=[],has_creep=lambda _:True,
              client=NS(_execute=AsyncMock(side_effect=query)),actions=[])
    return act


def test_native_invalid_point_is_skipped_even_with_creep():
    good=Point2((3.5,.5));act=scene([None,good])
    assert asyncio.run(act._ahead(Point2((0,0)),8,A.BUILD_CREEPTUMOR_TUMOR))==good
    request=act.ai.client._execute.call_args.kwargs['query']
    assert all(row.ability_id==A.BUILD_CREEPTUMOR_TUMOR.value for row in request.placements)


def test_recent_rejection_and_same_frame_cast_are_not_reused():
    bad=Point2((8,.5));good=Point2((2.5,.5));act=scene([bad,good])
    act._rejected_points=[(bad,10)]
    assert asyncio.run(act._ahead(Point2((0,0)),8,A.BUILD_CREEPTUMOR_TUMOR))==good
    act=scene([bad,bad,bad]);act.ai.actions=[NS(ability=A.BUILD_CREEPTUMOR_QUEEN,target=bad)]
    assert asyncio.run(act._ahead(Point2((0,0)),8,A.BUILD_CREEPTUMOR_TUMOR)) is None


def test_tumor_does_not_choose_a_place_beyond_its_cast_range():
    act=scene([Point2((11,0)),None,None])
    assert asyncio.run(act._ahead(Point2((0,0)),8,A.BUILD_CREEPTUMOR_TUMOR)) is None
