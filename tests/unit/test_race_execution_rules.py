"""Race-specific execution facts delegate without altering shared scheduling."""

from types import SimpleNamespace as NS
from unittest.mock import MagicMock
import ast
from pathlib import Path

import pytest
from sc2.ids.unit_typeid import UnitTypeId as U

from sc2bench_env.backends.sharpy.races.base import RaceAdapter
from sc2bench_env.backends.sharpy.races.terran import TerranAdapter
from tests.unit.test_execution_blockers import macro, parent


@pytest.mark.parametrize("method,args", [
    ("execution_blocker", (object(), {})),
    ("resource_committed", (object(), {})),
    ("ready_progress_target", ({},)),
    ("ability_energy_budget", (object(),)),
])
def test_missing_execution_support_is_explicit(method, args):
    with pytest.raises(NotImplementedError):
        getattr(RaceAdapter, method)(NS(), *args)


def test_macro_passes_live_task_and_state_to_constraint_and_payment_hooks():
    m = macro()
    task = {"action": "train", "target": "test_only"}
    calls = []
    def blocker(ai, received):
        calls.append(("blocker", ai, received))
        return "test_blocker"
    def payment(ai, received):
        calls.append(("payment", ai, received))
        received["_paid"] = True
        return True
    m.adapter = NS(execution_blocker=blocker, resource_committed=payment)
    assert m._execution_blocker(task) == "test_blocker"
    assert m._resource_committed(task) is True
    assert calls == [("blocker", m.ai, task), ("payment", m.ai, task)]
    assert all(received is task for _kind, _ai, received in calls)
    assert task["_paid"]  # private reconciliation acts on the original task


def test_macro_budget_progress_uses_adapter_mapping_and_existing_count_callback():
    m = macro()
    task = {"action": "train", "target": "test_only"}
    calls = []
    m.adapter = NS(ready_progress_target=lambda received: (calls.append(received), "test_counter")[1])
    m._count_ready = lambda key: 7 if key == "test_counter" else 0
    assert m._ready_progress(task) == 7
    assert calls == [task]
    m.adapter.ready_progress_target = lambda received: None
    m._count_ready = MagicMock(side_effect=AssertionError("no target must not be counted"))
    assert m._ready_progress(task) == 0
    m._count_ready.assert_not_called()


@pytest.mark.parametrize("hook,wrapper", [("execution_blocker", "_execution_blocker"),
    ("resource_committed", "_resource_committed"), ("ready_progress_target", "_ready_progress")])
def test_adapter_failures_are_not_replaced_with_runnable_or_unpaid_defaults(hook, wrapper):
    m = macro()
    def broken(*args):
        raise RuntimeError("controlled execution rule failure")
    setattr(m.adapter, hook, broken)
    with pytest.raises(RuntimeError, match="controlled execution rule failure"):
        getattr(m, wrapper)({"action": "train", "target": "marine"})


@pytest.mark.parametrize("target,parent_type", [("barracks_techlab", U.BARRACKS),
    ("barracks_reactor", U.BARRACKS), ("factory_techlab", U.FACTORY),
    ("factory_reactor", U.FACTORY), ("starport_techlab", U.STARPORT),
    ("starport_reactor", U.STARPORT)])
def test_all_addon_targets_keep_host_constraints(target, parent_type):
    m = macro(parent(type_id=parent_type, addon=8))
    task = {"action": "build", "target": target}
    assert m._execution_blocker(task) == "addon_host_unavailable"
    m.ai.structures[0].add_on_tag = 0
    assert m._execution_blocker(task) is None
    m.ai.structures[0].orders = [object()]
    assert m._execution_blocker(task) == "addon_host_busy"


@pytest.mark.parametrize("facility,reactor,target", [(U.BARRACKS, U.BARRACKSREACTOR, "marine"),
    (U.FACTORY, U.FACTORYREACTOR, "hellion"), (U.STARPORT, U.STARPORTREACTOR, "medivac")])
def test_each_producer_family_keeps_reactor_two_slot_rule(facility, reactor, target):
    addon = NS(tag=8, type_id=reactor, is_ready=True)
    m = macro(parent(type_id=facility, addon=8, orders=1), addon)
    task = {"action": "train", "target": target}
    assert m._execution_blocker(task) is None
    m.ai.structures[0].orders.append(object())
    assert m._execution_blocker(task) == "producer_busy"


@pytest.mark.parametrize("kind", [U.ORBITALCOMMAND, U.PLANETARYFORTRESS])
def test_scv_queue_uses_morphed_townhall_producer(kind):
    m = macro(parent(type_id=kind, orders=1))
    task = {"action": "train", "target": "scv"}
    assert m._execution_blocker(task) == "producer_busy"
    m.ai.structures[0].orders.clear()
    assert m._execution_blocker(task) is None


@pytest.mark.parametrize("action,target,expected_type", [("build", "command_center", U.COMMANDCENTER),
    ("build", "refinery", U.REFINERY), ("build", "barracks", U.BARRACKS),
    ("build", "factory_reactor", U.FACTORYREACTOR), ("train", "marine", U.MARINE)])
def test_pending_payment_probes_keep_target_ids_without_touching_demands(action, target, expected_type):
    m = macro()
    task = {"action": action, "target": target, "count": 3}
    original = dict(task)
    assert m._resource_committed(task) is False
    m.ai.already_pending.assert_called_once_with(expected_type)
    m.ai.already_pending.return_value = 1
    assert m._resource_committed(task) is True
    assert task == original


def test_stale_paid_train_is_cleared_without_cancelling_or_completing_demand():
    m = macro()
    task = {"action": "train", "target": "marine", "count": 3,
            "_paid": True, "_resource_committed": True}
    assert not m._resource_committed(task)
    assert task == {"action": "train", "target": "marine", "count": 3}
    task.update(_paid=True, _resource_committed=True)
    m.ai.already_pending.return_value = 1
    assert m._resource_committed(task)
    assert task["_paid"] and task["_resource_committed"]


@pytest.mark.parametrize("action,target,key", [("build", "command_center", "command_center"),
    ("build", "orbital_command", "command_center"), ("build", "planetary_fortress", "command_center"),
    ("build", "supply_depot", "supply_depot"), ("build", "barracks_techlab", "barracks_techlab"),
    ("train", "marine", "marine"), ("research", "stimpack", None),
    ("upgrade", "cc_0", None), ("train", "not_a_target", None)])
def test_budget_progress_mapping_preserves_existing_target_meaning(action, target, key):
    assert TerranAdapter().ready_progress_target({"action": action, "target": target}) == key


def test_prerequisite_aliases_are_race_metadata_without_generic_terran_fallback():
    assert set(TerranAdapter().prerequisite_count_keys("command_center")) == {
        "command_center", "orbital_command", "planetary_fortress"}
    assert TerranAdapter().prerequisite_count_keys("barracks") == ("barracks",)
    assert RaceAdapter.prerequisite_count_keys(NS(), "command_center") == ("command_center",)


@pytest.mark.parametrize("action,reason", [("scan", "prerequisite:orbital_command"),
    ("call_mule", "prerequisite:orbital_command"), ("scout", None)])
def test_ability_and_worker_prerequisites_keep_reporting_boundary(action, reason):
    m = macro()
    m._count_ready = lambda name: 0
    task = {"action": action, "_waiting_for": "old_reason"}
    assert m._prereq_blocked(task)
    assert task.get("_waiting_for") == reason
    m._count_ready = lambda name: 1
    assert not m._prereq_blocked(task)
    assert "_waiting_for" not in task


def test_energy_budget_is_per_caster_not_pooled_and_excludes_used_tags():
    ai = NS(structures=lambda kind: NS(ready=[NS(tag=1, energy=35), NS(tag=2, energy=50)]),
            unit_tags_received_action=set())
    adapter = TerranAdapter()
    assert adapter.ability_energy_budget(ai) == 50
    assert adapter.ability_energy_budget(ai, available_only=True) == 50
    ai.unit_tags_received_action.add(2)
    assert adapter.ability_energy_budget(ai, available_only=True) == 35
    ai.unit_tags_received_action.add(1)
    assert adapter.ability_energy_budget(ai, available_only=True) == 0


def test_macro_energy_budget_delegates_to_adapter_even_without_orbitals():
    import asyncio
    m = macro()
    calls = []
    m.adapter = NS(ability_energy_budget=lambda ai: (calls.append(ai), 0.0)[1])
    assert asyncio.run(m.execute())
    assert calls == [m.ai]


def test_shared_macro_has_no_terran_or_sc2_type_imports():
    from sc2bench_env.backends.sharpy import macro as module
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any("races.terran" in name or name.startswith("sc2.ids") for name in modules)
