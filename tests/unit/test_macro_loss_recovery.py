"""Paid markers must track live production, not survive a lost producer."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
_ensure_runtime_paths()
from sc2bench_env.backends.sharpy.macro import ActOngoingMacroTasks


def setup(pending=0, target="marine", missing="barracks"):
    act = SimpleNamespace(to_count=3, execute=AsyncMock(return_value=False))
    task = {"action": "train", "target": target, "to_count": 3,
            "_act": act, "_started": True, "_paid": True,
            "_resource_committed": True}
    macro = ActOngoingMacroTasks([task])
    structures = MagicMock()
    structures.return_value.ready = []
    macro.ai = SimpleNamespace(minerals=500, vespene=0, supply_cap=23, supply_used=13,
                               already_pending=MagicMock(return_value=pending),
                               structures=structures)
    macro._count_ready = lambda name: 0 if name == missing else 1
    return macro, task, act


@pytest.mark.parametrize("target,missing", [
    ("marine", "barracks"),
    ("marauder", "barracks_techlab"),
    ("ghost", "ghost_academy"),
])
def test_lost_producer_clears_stale_payment_and_reports_prerequisite(target, missing):
    macro, task, act = setup(target=target, missing=missing)
    macro.ai.vespene = 500
    asyncio.run(macro.execute())
    assert task["_waiting_for"] == f"prerequisite:{missing}"
    assert "_paid" not in task and "_resource_committed" not in task
    act.execute.assert_not_awaited()
    macro._count_ready = lambda name: 1
    asyncio.run(macro.execute())
    assert "_waiting_for" not in task
    act.execute.assert_awaited_once()


def test_existing_in_flight_production_keeps_payment():
    macro, task, _ = setup(pending=1)
    assert macro._resource_committed(task)
    assert task["_paid"] and task["_resource_committed"]


def test_new_unit_after_finished_or_lost_production_rechecks_resources():
    macro, task, act = setup()
    macro._count_ready = lambda name: 1
    macro.ai.minerals = 0
    asyncio.run(macro.execute())
    assert task["_waiting_for"] == "resources"
    act.execute.assert_not_awaited()
