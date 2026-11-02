"""Resting ground defenders make local space for a builder, without overriding combat."""
from types import SimpleNamespace as NS
import json
from pathlib import Path
import pytest
from sc2.position import Point2
from sc2.ids.unit_typeid import UnitTypeId as U
from sharpy.combat import Action
from tests.unit.test_combat_execution import UnitStub
from tests.unit.test_station_combat_priority import rules


def scene(kind):
    r=rules();unit=UnitStub(1,kind,position=(11,10))
    worker=UnitStub(2,U.DRONE,position=(10,10));worker.is_constructing_scv=False
    r.ai.workers=[worker];r.ai.in_pathing_grid=lambda _:True
    r.ai._macro_tasks=[{'action':'build','_act':NS(builder_tag=2,_last_build_order={'point':Point2((20,10))})}]
    return r,unit,worker


@pytest.mark.parametrize('kind',[U.MARINE,U.STALKER,U.ROACH])
def test_builder_crossing_produces_one_stable_local_yield(kind):
    r,unit,worker=scene(kind)
    result=r.guard_action(unit,Action(Point2((10,10)),False))
    point=result.target
    assert not result.is_attack and result.ability is None and unit.distance_to(point)<=2.5
    assert r.boundary(point) and abs(point.y-worker.position.y)>1
    assert r._builder_passage(unit)==point
    worker.position=Point2((20,10))
    assert r._builder_passage(unit) is None


def test_builder_yield_never_overrides_visible_combat():
    r,unit,_=scene(U.MARINE)
    enemy=UnitStub(3,U.ZERGLING,position=(13,10))
    r.ai.all_enemy_units=[enemy]
    command=Action(enemy,True)
    assert r.guard_action(unit,command) is command
    assert not r._builder_yields


def test_idle_worker_or_unreachable_yield_does_not_move_the_army():
    r,unit,_=scene(U.ROACH)
    r.ai.in_pathing_grid=lambda _:False
    assert r._builder_passage(unit) is None


def test_explicit_inject_queen_gets_a_clear_local_passage_and_releases_it_after_cast():
    r,unit,queen=scene(U.ROACH)
    queen.type_id=U.QUEEN;queen.radius=.875
    r.ai.workers=[];r.ai.units=[unit,queen]
    act=NS(_pending_cast={'caster':queen.tag,'point':Point2((20,10))})
    r.ai._macro_tasks=[{'action':'inject_larva','_act':act}]
    point=r._builder_passage(unit)
    assert point is not None and unit.distance_to(point)<=2.5
    blocker=UnitStub(3,U.ROACH,position=point);r.ai.units.append(blocker)
    r._builder_yields.clear()
    alternative=r._builder_passage(unit)
    assert alternative is not None and alternative.distance_to(blocker.position)>=1.1
    act._pending_cast=None
    assert r._builder_passage(unit) is None


def test_recorded_queen_crowd_clears_multiple_rows_without_colliding_destinations():
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures' / 'inject_round_110_crowd.json').read_text())
    r = rules()
    r.boundary = lambda _: True
    r.ai.workers = []
    r.ai.in_pathing_grid = lambda _: True
    units = [UnitStub(row['tag'], getattr(U, row['type']), position=row['position'],
                      radius=row['radius'], orders=[]) for row in fixture['nearby_ground']]
    r.ai.units = units
    queen = next(u for u in units if u.tag == fixture['caster'])
    goal = Point2(fixture['target'])
    act = NS(_pending_cast={'caster': queen.tag, 'point': goal})
    r.ai._macro_tasks = [{'action': 'inject_larva', '_act': act}]
    dx, dy = goal.x - queen.position.x, goal.y - queen.position.y
    length = (dx * dx + dy * dy) ** .5
    dx, dy = dx / length, dy / length
    cleared = []
    for unit in units:
        if unit.type_id not in {U.ROACH, U.HYDRALISK, U.ZERGLING}:
            continue
        ux, uy = unit.position.x - queen.position.x, unit.position.y - queen.position.y
        ahead, lateral = dx * ux + dy * uy, -dy * ux + dx * uy
        if not (0 < ahead < 6 and abs(lateral) < queen.radius + unit.radius + .2):
            continue
        point = r._builder_passage(unit)
        assert point is not None, (unit.type_id, unit.position, ahead)
        px, py = point.x - queen.position.x, point.y - queen.position.y
        assert abs(-dy * px + dx * py) >= queen.radius + unit.radius + .2
        assert all(point.distance_to(other.position) >= unit.radius + other.radius + .1
                   for other in units if other.tag != unit.tag)
        assert all(point.distance_to(other_point) >= unit.radius + other.radius + .1
                   for other, other_point in cleared)
        cleared.append((unit, point))
    assert any(unit.distance_to(queen) > 4 for unit, _ in cleared)
    # A row clears before the Queen reaches it, and stays clear until she passes.
    far, point = next((u, p) for u, p in cleared if u.distance_to(queen) > 4)
    far.position = point
    assert r._builder_passage(far) == point
    act._pending_cast = None
    assert r._builder_passage(far) is None


def test_queen_passage_does_not_move_distant_rows_or_defenders_behind_her():
    r, unit, queen = scene(U.ROACH)
    r.boundary = lambda _: True
    queen.type_id = U.QUEEN
    queen.radius = .875
    r.ai.workers = []
    r.ai.units = [unit, queen]
    r.ai._macro_tasks = [{'action': 'inject_larva', '_act': NS(
        _pending_cast={'caster': queen.tag, 'point': Point2((30, 10))})}]
    for position in [(20, 10), (7, 10), (13, 14)]:
        unit.position = Point2(position)
        assert r._builder_passage(unit) is None
    r.ai._macro_tasks=[]
    assert r._builder_passage(unit) is None
