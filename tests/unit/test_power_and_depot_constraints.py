"""Constraints observed in native matches must not appear as usable capacity."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import MagicMock
import pytest
import numpy as np
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.position import Point2
from sc2.units import Units
from sharpy.plans.tactics.terran import LowerDepots
from sc2bench_env.backends.sharpy.races import protoss_execution, protoss_production
from sc2bench_env.backends.sharpy.races.protoss import ProtossAdapter
from tests.unit.test_platform_audit_repairs import inventory, unit


@pytest.mark.parametrize('enemy',[False,True])
def test_depot_waits_for_ground_occupant_before_raising(enemy):
    depot=MagicMock(type_id=U.SUPPLYDEPOTLOWERED,is_ready=True,orders=[],position=Point2((10,10)))
    occupant=unit(2,U.ZERGLING if enemy else U.MARINE)
    occupant.position=Point2((10,10));occupant.is_flying=False;occupant.is_burrowed=False;occupant.radius=.375
    nearby=unit(3,U.ZERGLING);nearby.position=Point2((13,10));nearby.is_flying=False;nearby.radius=.375
    ai=inventory(*([] if enemy else [occupant]))
    ai._distance_units_to_pos=lambda units,point:np.array([u.position.distance_to(point) for u in units])
    ai.enemy_units=Units([nearby,*([occupant] if enemy else [])],ai)
    ai.all_units=Units([*ai.units,*ai.enemy_units],ai)
    act=LowerDepots();act.ai=ai
    act.cache=NS(own=lambda kind:Units([depot] if kind==U.SUPPLYDEPOTLOWERED else [],ai))
    asyncio.run(act.execute());depot.assert_not_called()
    occupant.position=Point2((7,10))
    asyncio.run(act.execute());depot.assert_called_once()


@pytest.mark.parametrize('kind,target',[(U.ROBOTICSFACILITY,'immortal'),(U.GATEWAY,'zealot'),(U.WARPGATE,'zealot')])
def test_unpowered_producer_has_explicit_blocker(kind,target):
    producer=unit(1,kind);producer.is_powered=False
    ai=inventory(structures=[producer])
    assert protoss_execution.execution_blocker(ai,{'action':'train','target':target})=='unpowered'


def test_unpowered_robo_has_no_free_production_slots():
    robo=unit(1,U.ROBOTICSFACILITY);robo.is_powered=False;robo.build_progress=1
    ai=inventory(structures=[robo])
    row=next(r for r in protoss_production.read_production_capacity(ai,ProtossAdapter()) if r['facility']=='robotics_facility')
    assert row['free_slots']==0 and row['unpowered']==1


def test_brief_production_reports_power_loss_and_distinguishes_unknown():
    from sc2bench_env.interface.observation_text import brief_facility_lines
    assert brief_facility_lines(None)==['unknown']
    assert brief_facility_lines([])==['none']
    row={'facility':'robotics_facility','ready_grounded':1,'capacity':0,'free_slots':0,'unpowered':1}
    text=' '.join(brief_facility_lines([row],race='protoss'))
    assert 'unpowered 1' in text and 'free slots 0/0' in text
