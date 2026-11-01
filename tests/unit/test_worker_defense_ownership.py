"""Emergency defense can recruit gatherers, without stealing an explicit role/order."""
import asyncio
from types import SimpleNamespace as NS
import pytest
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.position import Point2
from sc2.units import Units
from sharpy.managers.core.roles import UnitTask
from sc2bench_env.backends.sharpy.defense import PlanZoneDefenseSafe


def scene(race,kind,role,used=False,constructing=False,enemy_distance=0,health=45,shield=0):
    def worker(tag):
        return NS(tag=tag,type_id=kind,shield=shield,health=health,is_constructing_scv=False)
    protected,eligible=worker(1),worker(2)
    protected.is_constructing_scv=constructing
    bot=NS(_distance_units_to_pos=lambda units,point:[0 for _ in units])
    workers=Units([protected,eligible],bot)
    enemy=NS(type_id=U.ZEALOT,is_flying=False,position=Point2((enemy_distance,0)),distance_to=lambda _:enemy_distance)
    zone=NS(known_enemy_units=Units([enemy],bot),center_location=Point2((0,0)),
        our_workers=workers,our_wall=lambda:False)
    roles={1:role,2:UnitTask.Gathering};sent=[]
    act=PlanZoneDefenseSafe()
    act.ai=NS(workers=workers,supply_used=20,time=100,unit_tags_received_action={1} if used else set(),
              get_terrain_height=lambda _:0)
    act.knowledge=NS(my_race=race,enemy_race=Race.Protoss)
    act.roles=NS(unit_role=lambda w:roles[w.tag],set_task=lambda r,w:roles.__setitem__(w.tag,r))
    act.unit_values=NS(defense_value=lambda _:1)
    act.combat=NS(add_unit=lambda w:sent.append(w.tag))
    tags=[]
    asyncio.run(act.worker_defence(0,NS(power=20),Point2((0,0)),zone,tags,Units([],None)))
    return roles,sent,tags


@pytest.mark.parametrize('race,kind',[(Race.Terran,U.SCV),(Race.Protoss,U.PROBE),(Race.Zerg,U.DRONE)])
@pytest.mark.parametrize('role,used,constructing',[
    (UnitTask.Scouting,False,False),(UnitTask.Building,False,False),
    (UnitTask.Reserved,False,False),(UnitTask.Idle,True,False),(UnitTask.Idle,False,True)])
def test_worker_defense_preserves_other_owners_and_commands(race,kind,role,used,constructing):
    roles,sent,tags=scene(race,kind,role,used,constructing)
    assert roles[1]==role and 1 not in sent and 1 not in tags
    assert roles[2]==UnitTask.Defending and 2 in sent and 2 in tags


@pytest.mark.parametrize('race,kind,health,shield,expected',[
    (Race.Terran,U.SCV,45,0,True),(Race.Zerg,U.DRONE,40,0,True),
    (Race.Protoss,U.PROBE,20,20,True),(Race.Terran,U.SCV,5,0,False),
    (Race.Zerg,U.DRONE,5,0,False),(Race.Protoss,U.PROBE,20,0,False)])
def test_worker_health_guard_uses_the_races_actual_durability(race,kind,health,shield,expected):
    roles,sent,tags=scene(race,kind,UnitTask.Reserved,enemy_distance=7,health=health,shield=shield)
    assert (roles[2]==UnitTask.Defending)==expected
    assert (2 in sent)==expected and (2 in tags)==expected
