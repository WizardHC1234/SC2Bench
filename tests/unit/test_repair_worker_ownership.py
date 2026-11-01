"""Automatic repair must preserve dedicated worker tasks and real repair costs."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import MagicMock
import pytest
from sharpy.managers.core.roles import UnitTask
from sharpy.plans.tactics.terran.repair import Repair
from sc2.ids.unit_typeid import UnitTypeId as U


def scene(roles,gas=100):
    workers=[NS(tag=i+1,orders=[],is_repairing=False,is_constructing_scv=False,repair=MagicMock()) for i in range(len(roles))]
    target=NS(tag=100,type_id=U.MEDIVAC,is_ready=True,health_percentage=.5,is_structure=False,is_mechanical=True)
    zone=NS(our_power=NS(power=0),assaulting_enemy_power=NS(power=0),our_workers=workers,our_units=[target])
    act=Repair();act.zone_manager=NS(our_zones=[zone]);act.solve_scv_count=lambda *_:1
    act.roles=NS(unit_role=lambda w:roles[w.tag-1],set_task=MagicMock())
    act.ai=NS(minerals=500,vespene=gas,unit_tags_received_action=set(),calculate_unit_value=lambda _:NS(minerals=100,vespene=100))
    return act,workers


@pytest.mark.parametrize('dedicated',[UnitTask.Scouting,UnitTask.Building,UnitTask.Reserved])
def test_repair_selects_a_free_worker_instead_of_a_dedicated_worker(dedicated):
    act,workers=scene([dedicated,UnitTask.Gathering])
    asyncio.run(act.execute())
    workers[0].repair.assert_not_called();workers[1].repair.assert_called_once()


def test_repair_does_not_overwrite_an_action_sent_this_frame():
    act,workers=scene([UnitTask.Gathering,UnitTask.Gathering])
    act.ai.unit_tags_received_action={1}
    asyncio.run(act.execute())
    workers[0].repair.assert_not_called();workers[1].repair.assert_called_once()


def test_gas_cost_repair_waits_at_zero_gas_but_mineral_only_repair_can_continue():
    act,workers=scene([UnitTask.Gathering],gas=0)
    asyncio.run(act.execute());workers[0].repair.assert_not_called()
    act.ai.calculate_unit_value=lambda _:NS(minerals=400,vespene=0)
    asyncio.run(act.execute());workers[0].repair.assert_called_once()
