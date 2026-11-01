"""Holding slots stay off the ramp corridor and on the owned upper plateau."""
from types import SimpleNamespace as NS
from sc2.position import Point2
from sc2bench_env.backends.sharpy.holding import ramp_slots, high_ground_side


def fixture():
    zone=NS(center_location=Point2((0, -12)),ramp=NS(top_center=Point2((0, 0)),bottom_center=Point2((0, 6))))
    ai=NS(in_pathing_grid=lambda p: p.y<=0, get_terrain_height=lambda p: 10 if p.y<=0 else 0,structures=[])
    return ai,zone


def test_slots_leave_center_lane_open_and_use_high_ground():
    ai,zone=fixture()
    slots=ramp_slots(ai,zone,zone.center_location)
    assert len(slots)>5
    assert all(2<=abs(p.x)<=8 and -7<=p.y<=-1.5 for p in slots)
    assert not high_ground_side(ai,zone,zone.center_location,Point2((0, 3)))
    assert all(high_ground_side(ai,zone,zone.center_location,p) for p in slots)


def test_slots_avoid_structures_and_unpathable_ground():
    ai,zone=fixture();ai.structures=[NS(position=Point2((5,-7)),radius=2)]
    ai.in_pathing_grid=lambda p:p.x>=0
    slots=ramp_slots(ai,zone,zone.center_location)
    assert slots and all(p.x>=0 and p.distance_to(ai.structures[0].position)>=4 for p in slots)


def test_lower_base_does_not_borrow_the_main_base_high_ramp():
    ai,zone=fixture();zone.center_location=Point2((0,12))
    assert ramp_slots(ai,zone,zone.center_location)==[]


def test_ramp_center_can_be_sloped_below_the_connected_plateau():
    ai,zone=fixture()
    ai.get_terrain_height=lambda p: 8 if p.y>=0 else 10
    slots=ramp_slots(ai,zone,zone.center_location)
    assert slots and all(ai.get_terrain_height(p)==10 for p in slots)


def test_two_base_exits_share_one_central_station():
    ai,zone=fixture()
    left=NS(top_center=Point2((-8,0)),bottom_center=Point2((-8,6)))
    right=NS(top_center=Point2((8,0)),bottom_center=Point2((8,6)))
    zone.ramp=left;ai.game_info=NS(map_ramps=[left,right])
    from sc2bench_env.backends.sharpy.holding import base_ramps
    assert len(base_ramps(ai,zone,zone.center_location))==2
    station=ramp_slots(ai,zone,zone.center_location)[0]
    assert abs(station.x)<2 and station.y<0


def test_two_exit_rule_rotates_with_the_map():
    ai=NS(in_pathing_grid=lambda p:p.x>=0,get_terrain_height=lambda p:10 if p.x>=0 else 0,structures=[])
    first=NS(top_center=Point2((0,-8)),bottom_center=Point2((-6,-8)))
    second=NS(top_center=Point2((0,8)),bottom_center=Point2((-6,8)))
    zone=NS(center_location=Point2((12,0)),ramp=first);ai.game_info=NS(map_ramps=[first,second])
    station=ramp_slots(ai,zone,zone.center_location)[0]
    assert station.x>0 and abs(station.y)<2


def test_base_between_uphill_and_downhill_exits_uses_both():
    up=NS(top_center=Point2((-8,6)),bottom_center=Point2((-8,0)))
    down=NS(top_center=Point2((8,0)),bottom_center=Point2((8,6)))
    ai=NS(structures=[],in_pathing_grid=lambda p:p.y<=0,
          get_terrain_height=lambda p:10 if p.y<=0 else (26 if p.x<0 else -6),
          game_info=NS(map_ramps=[up,down]))
    zone=NS(center_location=Point2((0,-12)),ramp=down)
    from sc2bench_env.backends.sharpy.holding import base_ramps
    assert len(base_ramps(ai,zone,zone.center_location))==2
    station=ramp_slots(ai,zone,zone.center_location)[0]
    assert abs(station.x)<2


def test_rear_entrance_is_excluded_when_forward_entrances_exist():
    from sc2bench_env.backends.sharpy.holding import base_ramps
    left=NS(top_center=Point2((-8,0)),bottom_center=Point2((-8,6)))
    right=NS(top_center=Point2((8,0)),bottom_center=Point2((8,6)))
    rear=NS(top_center=Point2((0,-24)),bottom_center=Point2((0,-30)))
    zone=NS(center_location=Point2((0,-12)),ramp=left)
    for enemy, expected in ((Point2((0,100)), [left,right]),
                            (Point2((0,-100)), [rear])):
        ai=NS(structures=[],in_pathing_grid=lambda p:-24<=p.y<=0,
              get_terrain_height=lambda p:10 if -24<=p.y<=0 else 0,
              game_info=NS(map_ramps=[left,right,rear]),enemy_start_locations=[enemy])
        ramps=base_ramps(ai,zone,zone.center_location)
        assert {tuple(r.top_center) for r in ramps}=={tuple(r.top_center) for r in expected}
