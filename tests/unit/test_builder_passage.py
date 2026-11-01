"""Resting ground defenders make local space for a builder, without overriding combat."""
from types import SimpleNamespace as NS
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
    r.ai._macro_tasks=[]
    assert r._builder_passage(unit) is None
