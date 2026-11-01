"""Gas placement must compare candidates instead of picking the final geyser."""
from types import SimpleNamespace as NS
from itertools import permutations
import pytest
from sc2.position import Point2
from sc2.ids.ability_id import AbilityId as A
from sharpy.plans.acts import BuildGas


def gas(tag,position,contents=2250):
    result=NS(tag=tag,position=Point2(position),vespene_contents=contents)
    result.distance_to=lambda p:result.position.distance_to(getattr(p,'position',p))
    return result


@pytest.mark.parametrize('order',list(permutations([0,1,2])))
def test_prefers_remaining_gas_then_distance_regardless_of_iteration_order(order):
    act=BuildGas(2)
    sites=[gas(1,(4,0)),gas(2,(-4,0)),gas(3,(24,0))]
    hall=NS(is_ready=True,build_progress=1,position=Point2((10,0)))
    # The first main geyser is occupied; the second one remains eligible.
    ordered=[sites[i] for i in order]
    act.ai=NS(townhalls=[hall],vespene_geyser=NS(closer_than=lambda radius,unit:
        [g for g in ordered if g.distance_to(unit)<radius]))
    act.cache=NS(own=lambda _: [sites[0]],enemy=lambda _:[])
    act.zone_manager=NS(own_main_zone=NS(center_location=Point2((0,0))))
    act.find_best()
    assert act.best_gas is sites[1]


@pytest.mark.parametrize('ability',[A.TERRANBUILD_REFINERY,A.PROTOSSBUILD_ASSIMILATOR,A.ZERGBUILD_EXTRACTOR])
def test_gas_order_already_submitted_this_frame_reserves_its_geyser(ability):
    act=BuildGas(2)
    sites=[gas(1,(4,0)),gas(2,(-5,0))]
    hall=NS(is_ready=True,build_progress=1,position=Point2((0,0)))
    act.ai=NS(townhalls=[hall],vespene_geyser=NS(closer_than=lambda *_:sites),actions=[])
    act.cache=NS(own=lambda _:[],enemy=lambda _:[])
    act.zone_manager=NS(own_main_zone=NS(center_location=Point2((0,0))))
    act.find_best()
    assert act.best_gas is sites[0]
    act.ai.actions=[NS(ability=ability,target=sites[0])]
    act.find_best()
    assert act.best_gas is sites[1]
    act.ai.actions=[NS(ability=A.HARVEST_GATHER,target=sites[0])]
    act.find_best()
    assert act.best_gas is sites[0]
