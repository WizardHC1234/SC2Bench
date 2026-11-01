"""Home defenders settle into available space instead of a fixed radius ring."""
from types import SimpleNamespace as NS
import pytest
from sc2.position import Point2
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.ids.ability_id import AbilityId as A
from sharpy.combat import Action,NoAction
from sc2bench_env.backends.sharpy.micro import MissionMicroRules
from tests.unit.test_combat_execution import UnitStub


def rules():
    r=MissionMicroRules();r.hold_position=True;r.compact_station=True
    r.return_point=Point2((0,0));r.boundary=lambda p:p.distance_to(r.return_point)<20
    r.ai=NS(all_enemy_units=[],units=[],workers=[])
    return r


def test_mobile_unit_four_tiles_from_station_keeps_approaching():
    r=rules();u=UnitStub(1,position=(4,0))
    a=r.guard_action(u,NoAction(),group_units=[u])
    assert a.position==r.return_point and not a.is_attack


def test_close_settled_neighbor_allows_compact_collision_limited_arrival():
    r=rules();inner=UnitStub(1,position=(.7,0),radius=.5)
    outer=UnitStub(2,position=(1.8,0),radius=.5)
    assert r.at_station(inner)
    assert r.at_station(outer,group_units=[inner,outer])
    assert isinstance(r.guard_action(outer,NoAction(),group_units=[inner,outer]),NoAction)


def test_same_radius_neighbors_cannot_anchor_an_empty_ring():
    r=rules();left=UnitStub(1,position=(4,.4));right=UnitStub(2,position=(4,-.4))
    assert not r.at_station(left,group_units=[left,right])
    assert not r.at_station(right,group_units=[left,right])


def test_settled_unit_does_not_repack_when_a_neighbor_is_dispatched():
    r=rules();a=UnitStub(1,position=(.7,0));b=UnitStub(2,position=(1.8,0))
    assert r.at_station(a)
    assert r.at_station(b,group_units=[a,b])
    assert isinstance(r.guard_action(b,NoAction(),group_units=[b]),NoAction)


def test_moving_neighbor_cannot_cause_premature_settling():
    r=rules();a=UnitStub(1,position=(.7,0));b=UnitStub(2,position=(1.8,0))
    assert r.at_station(a)
    a.orders=[NS(ability=NS(id=A.MOVE_MOVE))]
    assert not r.at_station(b,group_units=[a,b])


@pytest.mark.parametrize('kind',[U.SIEGETANKSIEGED,U.LURKERMPBURROWED,U.WIDOWMINEBURROWED])
def test_deployed_defender_is_not_repositioned_for_compaction(kind):
    r=rules();u=UnitStub(1,kind,position=(4,0))
    assert isinstance(r.guard_action(u,NoAction(),group_units=[u]),NoAction)


def test_local_combat_keeps_priority_over_compaction():
    r=rules();u=UnitStub(1,position=(4,0));enemy=UnitStub(2,position=(8,0))
    r.ai.all_enemy_units=[enemy]
    a=Action(enemy,True)
    assert r.guard_action(u,a,group_units=[u]) is a


def test_new_station_resets_previous_settlement():
    r=rules();u=UnitStub(1,position=(.7,0))
    assert r.at_station(u)
    r.return_point=Point2((10,0))
    assert not r.at_station(u)


def test_nonhome_defense_and_attack_keep_their_existing_commands():
    r=rules();u=UnitStub(1,position=(4,0));r.compact_station=False
    assert isinstance(r.guard_action(u,NoAction()),NoAction)
    r.boundary=None;r.return_point=None;r.hold_position=False
    a=Action(Point2((50,0)),True)
    assert r.guard_action(u,a) is a
