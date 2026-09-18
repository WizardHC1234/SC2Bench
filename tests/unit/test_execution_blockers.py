"""Observed production constraints; no strategy, guessed money or queue edits."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
_ensure_runtime_paths()
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2bench_env.backends.sharpy.macro import ActOngoingMacroTasks
from sc2bench_env.interface.observation_text import section


def parent(tag=1, addon=0, orders=0, flying=False, type_id=U.BARRACKS):
    return SimpleNamespace(tag=tag, add_on_tag=addon, orders=[object()] * orders,
                           is_flying=flying, is_ready=True, type_id=type_id)


class Structures(list):
    def __call__(self, type_id):
        return SimpleNamespace(ready=[unit for unit in self if unit.type_id == type_id])


def macro(*units):
    result = ActOngoingMacroTasks([])
    result.ai = SimpleNamespace(structures=Structures(units), minerals=500, vespene=500,
                                supply_cap=30, supply_used=15,
                                already_pending=MagicMock(return_value=0))
    return result


@pytest.mark.parametrize("units,reason", [
    ([parent(addon=7)], "addon_host_unavailable"),
    ([parent(flying=True)], "addon_host_unavailable"),
    ([parent(orders=1)], "addon_host_busy"),
    ([parent(orders=1), parent(tag=2)], None),
])
def test_addon_host_constraints(units, reason):
    assert macro(*units)._execution_blocker({"action": "build", "target": "barracks_techlab"}) == reason


@pytest.mark.parametrize("orders,reason", [(1, None), (2, "producer_busy")])
def test_reactor_two_slots_not_one(orders, reason):
    reactor = SimpleNamespace(tag=7, type_id=U.BARRACKSREACTOR, is_ready=True)
    m = macro(parent(addon=7, orders=orders), reactor)
    assert m._execution_blocker({"action": "train", "target": "marine"}) == reason


def test_only_attached_techlab_producer_can_supply_marauder_slot():
    lab = SimpleNamespace(tag=7, type_id=U.BARRACKSTECHLAB, is_ready=True)
    m = macro(parent(addon=7, orders=1), parent(tag=2), lab)
    assert m._execution_blocker({"action": "train", "target": "marauder"}) == "producer_busy"
    m.ai.structures[0].orders.clear()
    assert m._execution_blocker({"action": "train", "target": "marauder"}) is None


def test_flying_producer_and_unknown_runtime_are_not_confused():
    m = macro(parent(flying=True))
    assert m._execution_blocker({"action": "train", "target": "marine"}) == "producer_unavailable"
    m.ai.structures[0].is_flying = None
    assert m._execution_blocker({"action": "train", "target": "marine"}) is None
    assert m._execution_blocker({"action": "build", "target": "barracks_techlab"}) is None


def test_unavailable_addon_does_not_reserve_or_block_later_runnable_train():
    lab = SimpleNamespace(tag=7, type_id=U.BARRACKSTECHLAB, is_ready=True)
    m = macro(parent(addon=7), lab)
    m.ai.minerals, m.ai.vespene = 50, 0
    m._count_ready = lambda name: 1
    m._ready_progress = lambda task: 0
    blocked = SimpleNamespace(execute=AsyncMock(return_value=False))
    runnable = SimpleNamespace(execute=AsyncMock(return_value=False))
    first = {"action": "build", "target": "barracks_techlab", "order_index": 1,
             "_act": blocked, "_started": True}
    second = {"action": "train", "target": "marine", "order_index": 2,
              "_act": runnable, "_started": True}
    m.active_tasks = [first, second]
    asyncio.run(m.execute())
    assert first["_waiting_for"] == "addon_host_unavailable"
    blocked.execute.assert_not_awaited()
    runnable.execute.assert_awaited_once()


def test_paid_train_still_reports_full_slot_without_discarding_payment():
    m = macro(parent(orders=1))
    m.ai.already_pending.return_value = 1
    m._ready_progress = lambda task: 0
    act = SimpleNamespace(execute=AsyncMock(return_value=False))
    task = {"action": "train", "target": "marine", "_act": act, "_started": True,
            "_paid": True, "_resource_committed": True}
    m.active_tasks = [task]
    asyncio.run(m.execute())
    assert task["_waiting_for"] == "producer_busy" and task["_paid"]
    act.execute.assert_awaited_once()


def test_unreported_blocker_is_unknown_not_none_in_model_text():
    lines = section("building", {"barracks_techlab": {"waiting_to_start": 1}})
    assert lines[-1].endswith("| unknown")
    lines = section("building", {"barracks_techlab": {"waiting_for": "none"}})
    assert lines[-1].endswith("| none")
