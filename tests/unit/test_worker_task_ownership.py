"""Scouting and construction cannot own the same worker concurrently."""
import asyncio
from types import SimpleNamespace as NS
import pytest
from sc2.position import Point2
from sc2.ids.unit_typeid import UnitTypeId as U
from sharpy.managers.core.roles import UnitTask
from sharpy.plans.acts import GridBuilding
from sc2bench_env.backends.sharpy.acts import ActScoutRoute


class Workers(list):
    def filter(self,predicate):return Workers(w for w in self if predicate(w))
    def sorted_by_distance_to(self,p):return Workers(sorted(self,key=lambda w:w.distance_to(p)))
    def find_by_tag(self,tag):return next((w for w in self if w.tag==tag),None)
    @property
    def gathering(self):return self
    @property
    def idle(self):return Workers()
    @property
    def exists(self):return bool(self)
    @property
    def first(self):return self[0]
    def __or__(self,other):return Workers([*self,*other])


def fixture(kind):
    roles={1:UnitTask.Idle,2:UnitTask.Idle}
    manager=NS(unit_role=lambda w:roles[w.tag],set_task=lambda r,w:roles.__setitem__(w.tag,r),
               clear_task=lambda tag:roles.__setitem__(getattr(tag,'tag',tag),UnitTask.Idle))
    ai=NS(unit_tags_received_action=set(),start_location=Point2((0,0)),
          is_visible=lambda _:True)
    workers=Workers()
    for tag,x in [(1,4),(2,-4)]:
        w=NS(tag=tag,type_id=kind,position=Point2((x,0)),is_constructing_scv=False,
             has_buff=lambda _:False,is_gathering=False,is_carrying_vespene=False,
             is_carrying_minerals=False)
        w.distance_to=lambda p,w=w:w.position.distance_to(getattr(p,'position',p))
        w.move=lambda p,w=w:ai.unit_tags_received_action.add(w.tag)
        workers.append(w)
    ai.workers=workers
    ai.zone_registry=NS(resolve_zone=lambda *_:NS(center_location=Point2((40,0))),center_for=lambda _:None)
    scout=ActScoutRoute(['zone_1']);scout.ai=ai;scout.roles=manager;scout.zone_manager=NS()
    build=GridBuilding(U.PYLON,1);build.ai=ai;build.roles=manager
    build.cache=NS(by_tag=workers.find_by_tag)
    return ai,scout,build,roles


@pytest.mark.parametrize('kind',[U.SCV,U.PROBE,U.DRONE])
def test_scout_keeps_its_worker_and_building_can_use_another(kind):
    ai,scout,build,roles=fixture(kind)
    assert asyncio.run(scout.execute()) is False
    assert scout._scout_tag==1
    builder=build.get_worker_builder(Point2((4,0)),None,build.only_roles)
    assert builder.tag==2
    assert roles[1]==UnitTask.Scouting


def test_builders_do_not_steal_dedicated_workers_when_no_free_worker_exists():
    ai,scout,build,roles=fixture(U.PROBE)
    roles[1]=UnitTask.Scouting;roles[2]=UnitTask.Building
    assert build.get_worker_builder(Point2((4,0)),None) is None


def test_builder_already_commanded_this_step_waits_instead_of_overwriting():
    ai,scout,build,roles=fixture(U.PROBE)
    roles[1]=UnitTask.Building;roles[2]=UnitTask.Reserved
    ai.unit_tags_received_action.add(1)
    assert build.get_worker_builder(Point2((4,0)),1) is None


def test_completed_route_releases_worker_to_building_or_mining():
    ai,scout,build,roles=fixture(U.PROBE)
    asyncio.run(scout.execute())
    ai.workers[0].position=Point2((40,0));ai.unit_tags_received_action.clear()
    assert asyncio.run(scout.execute())
    assert roles[1]==UnitTask.Idle


def test_removing_replaced_scout_task_releases_its_worker():
    from sc2bench_env.backends.sharpy.bot import BenchBot
    ai,scout,build,roles=fixture(U.PROBE)
    asyncio.run(scout.execute())
    bot=object.__new__(BenchBot)
    bot.bridge=NS(consume_discard_macro_tasks=lambda:False,get_macro_specs=lambda:[])
    bot._macro_tasks=[dict(task_id='old_scout',action='scout',_act=scout)]
    bot._sync_macro_tasks()
    assert roles[1]==UnitTask.Idle and not bot._macro_tasks
