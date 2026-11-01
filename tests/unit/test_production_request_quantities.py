"""A production request counts outputs, rather than replacing unrelated losses."""
import asyncio
from types import SimpleNamespace as NS

from sc2bench_env.backends.base import BackendSnapshot
from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
_ensure_runtime_paths()
from sc2bench_env.backends.sharpy.macro import ActOngoingMacroTasks
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId as U
from sharpy.plans.acts.act_unit import ActUnit


def setup(owned=16, pending=1, count=1, completed=0, old_goal=18):
    snapshot = BackendSnapshot(units={"scv": owned})
    bridge = NS(snapshot=snapshot, task_meta={"a": ("train", "scv", count, 1)}, peak_ready={"a": completed})
    ai = NS(bridge=bridge, minerals=1000, vespene=1000, supply_cap=200, supply_used=20)
    sim = NS(pending=pending, issued=0)

    class Act:
        to_count = old_goal

        async def execute(self):
            if bridge.snapshot.units["scv"] + sim.pending < self.to_count:
                sim.issued += 1
                sim.pending += 1
                ai.minerals -= 50
                return False
            return True

    act = Act()
    task = {"task_id": "a", "action": "train", "target": "scv", "to_count": old_goal,
            "order_index": 0, "_act": act, "_started": True}
    macro = ActOngoingMacroTasks([task])
    macro.ai = ai
    macro.adapter = NS(ability_energy_budget=lambda _ai, **kw: 0,
                       production_owned_count=lambda snapshot, action, target: snapshot.owned_count(action, target))
    macro._resource_committed = lambda _task: False
    macro._prereq_blocked = lambda _task: False
    macro._execution_blocker = lambda _task: None
    macro._ready_progress = lambda _task: bridge.snapshot.units["scv"]
    return macro, task, act, sim, bridge


def test_unrelated_scout_loss_does_not_add_another_worker_order():
    macro, task, act, sim, _ = setup()
    asyncio.run(macro.execute())
    assert sim.issued == 0
    assert task["to_count"] == act.to_count == 17


def test_remaining_request_still_runs_and_stops_after_its_birth():
    macro, _, _, sim, bridge = setup(pending=0, old_goal=17)
    asyncio.run(macro.execute())
    assert sim.issued == 1
    bridge.snapshot.units["scv"] += 1
    sim.pending = 0
    bridge.peak_ready["a"] = 1
    asyncio.run(macro.execute())
    assert sim.issued == 1


def test_completed_old_request_does_not_inflate_the_next_request_target():
    macro, first, _, sim, bridge = setup(count=2, completed=2, old_goal=19)
    second = {**first, "task_id": "b", "order_index": 1, "to_count": 20}
    macro.active_tasks.append(second)
    bridge.task_meta["b"] = ("train", "scv", 2, 1)
    bridge.peak_ready["b"] = 1
    asyncio.run(macro.execute())
    assert sim.issued == 0
    assert second["to_count"] == 17


def test_new_request_uses_only_unfulfilled_prefix_with_current_owned_count():
    macro, first, _, _, bridge = setup(pending=0, count=3, completed=1)
    second = {**first, "task_id": "b", "order_index": 1}
    macro.active_tasks.append(second)
    bridge.task_meta["b"] = ("train", "scv", 2, 1)
    bridge.peak_ready["b"] = 0
    asyncio.run(macro.execute())
    assert first["to_count"] == 18
    assert second["to_count"] == 20


def test_train_inventory_includes_transport_garrison_and_pending_without_duplicates():
    class Act(ActUnit):
        @property
        def builders(self):
            return [NS(orders=[NS(ability=NS(id=123))])]

    marine = lambda tag: NS(tag=tag, type_id=U.MARINE)
    shared = marine(3)
    visible = marine(1)
    carrier = NS(tag=2, type_id=U.MEDIVAC, passengers=[visible, shared])
    bunker = NS(tag=5, type_id=U.BUNKER, passengers=[shared, marine(4)])
    act = Act(U.MARINE, U.BARRACKS, to_count=5)
    act.ai = NS(units=[visible, carrier], structures=[bunker],
                _game_data=NS(units={U.MARINE.value: NS(creation_ability=NS(id=123))}))
    act.knowledge = NS(unit_values=NS(real_type=lambda value: value),
                       my_worker_type=U.SCV, my_race=Race.Terran)
    # Three unique completed Marines plus one native production order.
    assert act.get_unit_count() == 4
