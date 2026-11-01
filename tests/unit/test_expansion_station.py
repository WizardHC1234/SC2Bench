"""A stationed army must leave room for the next townhall, for every race."""
from types import SimpleNamespace as NS
import pytest
from sc2.position import Point2
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.ids.ability_id import AbilityId as A
from sharpy.combat import Action, NoAction
from sc2bench_env.backends.sharpy.holding import expansion_station, overlaps_townhall_site
from sc2bench_env.backends.sharpy.micro import MissionMicroRules
from tests.unit.test_combat_execution import UnitStub


def test_station_avoids_the_site_minerals_and_geysers_and_is_stable():
    center = Point2((30,30))
    ai = NS(structures=[], mineral_field=[NS(position=center.offset((-6,0)),radius=.75)],
            vespene_geyser=[NS(position=center.offset((0,7)),radius=1.5)],
            in_pathing_grid=lambda p:True, get_terrain_height=lambda p:10)
    station = expansion_station(ai, center)
    assert station.x > center.x
    assert not overlaps_townhall_site(station, center, 5)
    ai.units = [NS(position=station)]
    assert expansion_station(ai, center) == station


def test_station_stays_on_this_plateau_and_changes_if_a_building_occupies_it():
    center = Point2((30,30))
    ai = NS(structures=[], in_pathing_grid=lambda p:p.x>=30,
            get_terrain_height=lambda p:10 if p.y>=30 else 0)
    first = expansion_station(ai,center)
    assert first.x>=30 and first.y>=30
    ai.structures.append(NS(position=first,radius=2))
    second = expansion_station(ai,center)
    assert second.distance_to(first)>=5
    assert not overlaps_townhall_site(second,center,5)


def rules():
    result = MissionMicroRules()
    result.ai = NS(all_enemy_units=[])
    result.hold_position = True
    result.return_point = Point2((8,0))
    result.boundary = lambda p:True
    result.reserved_base_center = Point2((0,0))
    return result


@pytest.mark.parametrize('kind',[U.MARINE,U.STALKER,U.ROACH])
def test_stationed_ground_units_leave_site_even_if_micro_returns_no_action(kind):
    unit = UnitStub(1,kind,position=(0,0))
    command = rules().guard_action(unit,NoAction())
    assert command.position == Point2((8,0)) and not command.is_attack


def test_stationed_sieged_tank_unsieges_to_clear_site():
    tank = UnitStub(1,U.SIEGETANKSIEGED,position=(0,0))
    assert rules().guard_action(tank,NoAction()).ability == A.UNSIEGE_UNSIEGE


def test_air_units_do_not_need_to_clear_ground_footprint():
    unit = UnitStub(1,U.MEDIVAC,position=(0,0),is_flying=True)
    command = NoAction()
    assert rules().guard_action(unit,command) is command


def test_visible_combat_keeps_priority_over_clearing_the_site():
    r = rules()
    unit = UnitStub(1,U.STALKER,position=(0,0),sight_range=11)
    enemy = UnitStub(2,U.MARINE,position=(5,0))
    r.ai.all_enemy_units=[enemy]
    command=Action(enemy,True)
    assert r.guard_action(unit,command) is command
