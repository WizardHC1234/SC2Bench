"""Blink must respect native terrain, visibility, range and occupied space."""
from types import SimpleNamespace as NS
import pytest
from sc2.position import Point2
from sc2.ids.unit_typeid import UnitTypeId as U
from sharpy.combat.protoss.micro_stalkers import MicroStalkers
from tests.unit.test_combat_execution import UnitStub


def fixture(**checks):
    micro=MicroStalkers()
    micro.ai=NS(time=1,state=NS(game_loop=22),all_units=[],
        in_map_bounds=checks.get('bounds',lambda _:True),is_visible=checks.get('visible',lambda _:True),
        in_pathing_grid=checks.get('walkable',lambda _:True))
    unit=UnitStub(1,U.STALKER,position=(0,0),radius=.625)
    return micro,unit


@pytest.mark.parametrize('checks,point',[
    ({'bounds':lambda _:False},(4,0)),({'visible':lambda _:False},(4,0)),
    ({'walkable':lambda _:False},(4,0)),({},(9,0))])
def test_illegal_landings_do_not_replace_the_combat_command(checks,point):
    micro,unit=fixture(**checks)
    assert micro.blink_action(unit,Point2(point)) is None


@pytest.mark.parametrize('flying,allowed',[(False,False),(True,True)])
def test_ground_occupants_block_landing_while_flying_units_do_not(flying,allowed):
    micro,unit=fixture()
    micro.ai.all_units=[UnitStub(2,position=(4,0),radius=.5,is_flying=flying)]
    assert (micro.blink_action(unit,Point2((4,0))) is not None)==allowed


def test_two_stalkers_do_not_choose_the_same_landing_tile_in_one_frame():
    micro,unit=fixture();point=Point2((4,0))
    assert micro.blink_action(unit,point) is not None
    assert micro.blink_action(UnitStub(2,U.STALKER,position=(0,1)),point) is None
    micro.ai.state.game_loop+=10
    assert micro.blink_action(unit,point) is not None
