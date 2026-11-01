"""A rejected native order cannot remain paid and bypass resource priority."""
from types import SimpleNamespace as NS
import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from sc2bench_env.backends.sharpy.macro import ActOngoingMacroTasks


@pytest.mark.parametrize('race,target',[('terran','barracks'),('protoss','stargate'),('zerg','roach_warren')])
def test_explicit_build_rejection_discards_local_payment_markers(race,target):
    task=dict(action='build',target=target,_paid=True,_resource_committed=True,
              _act=NS(waiting_reason='build_location_rejected'))
    macro=ActOngoingMacroTasks([task],race=race)
    macro.ai=NS(minerals=500,vespene=0)
    macro._mark_paid_if_spent(task,500,0)
    assert '_paid' not in task and '_resource_committed' not in task
    assert task['action']=='build' and task['target']==target


def test_rejected_build_resumes_its_resource_priority_on_the_next_frame():
    build=NS(waiting_reason=None)
    async def reject():
        build.waiting_reason='build_location_rejected'
        return False
    build.execute=AsyncMock(side_effect=reject)
    train=NS(execute=AsyncMock(return_value=False))
    first=dict(action='build',target='stargate',order_index=1,_act=build,_started=True,
               _paid=True,_resource_committed=True)
    second=dict(action='train',target='stalker',order_index=2,_act=train,_started=True)
    macro=ActOngoingMacroTasks([first,second],race='protoss')
    structures=MagicMock();structures.return_value.ready=[]
    macro.ai=NS(minerals=1000,vespene=100,supply_cap=50,supply_used=20,
                already_pending=lambda _:0,structures=structures)
    macro._count_ready=lambda _:1
    macro._ready_progress=lambda _:0
    asyncio.run(macro.execute())
    train.execute.assert_awaited_once()  # Invalid locations do not reserve money.
    build.execute.reset_mock();train.execute.reset_mock()
    asyncio.run(macro.execute())
    assert first['_waiting_for']=='resources' and second['_waiting_for']=='resources'
    build.execute.assert_not_awaited();train.execute.assert_not_awaited()
