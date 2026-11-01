"""A MULE cannot be sent to gas while a refinery is missing workers."""
from types import SimpleNamespace as NS
from sc2.ids.unit_typeid import UnitTypeId as U
from sharpy.plans.tactics.distribute_workers import DistributeWorkers, WorkStatus
import pytest


def test_idle_mule_keeps_a_mineral_job_when_gas_has_higher_priority():
    act=DistributeWorkers()
    mineral=NS(type_id=U.COMMANDCENTER,has_vespene=False)
    gas=NS(type_id=U.REFINERY,has_vespene=True)
    act.work_queue=[WorkStatus(mineral,2),WorkStatus(gas,3)]
    assert act.get_new_work(NS(type_id=U.MULE)) is mineral
    assert act.work_queue[1].available==3
    assert act.get_new_work(NS(type_id=U.SCV)) is gas


def test_mule_with_no_mineral_job_is_not_sent_to_a_refinery():
    act=DistributeWorkers()
    gas=NS(type_id=U.REFINERY,has_vespene=True)
    act.work_queue=[WorkStatus(gas,3)]
    assert act.get_new_work(NS(type_id=U.MULE)) is None
    assert act.work_queue[0].available==3


@pytest.mark.parametrize('workers,gas,expected',[(18,2,6),(16,2,6),(8,2,4),(5,2,2),(12,1,3),(12,0,0)])
def test_built_gas_staffing_with_mineral_worker_reserve(workers,gas,expected):
    act=DistributeWorkers(fill_built_gas=True)
    act.roles=NS(free_workers=NS(amount=workers))
    act.ai=NS(gas_buildings=NS(ready=NS(amount=gas)))
    assert act.calc_gas_workers_target()==expected
