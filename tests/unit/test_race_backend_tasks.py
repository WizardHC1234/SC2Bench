"""Absolute Act targets and ability hints are not task completion evidence."""

import ast
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from sc2bench_env.backends.base import BackendSnapshot
from sc2bench_env.backends.sharpy.backend import SharpyBackend
from sc2bench_env.backends.sharpy.races.base import RaceAdapter
from sc2bench_env.backends.sharpy.races.terran import TerranAdapter
from sc2bench_env.runtime.task import Demand, DemandState


def snapshot():
    return BackendSnapshot(units={"marine": 5},
        buildings={"command_center": 1, "orbital_command": 2, "planetary_fortress": 1, "barracks": 1},
        info={"under_construction": {"command_center": 1, "orbital_command": 1, "barracks": 1}})


@pytest.mark.parametrize("action,target,expected", [("build", "command_center", 6),
    ("build", "orbital_command", 3), ("build", "planetary_fortress", 1),
    ("build", "barracks", 2), ("train", "marine", 5), ("train", "marauder", 0)])
def test_terran_absolute_target_owned_count_preserves_existing_facts(action, target, expected):
    state = snapshot()
    original = asdict(state)
    assert TerranAdapter().production_owned_count(state, action, target) == expected
    assert asdict(state) == original


def test_generic_owned_count_never_adds_terran_base_variants():
    assert RaceAdapter.production_owned_count(NS(), snapshot(), "build", "command_center") == 2


def test_interleaved_submission_keeps_extra_counts_order_and_factory_capture():
    backend = SharpyBackend()
    adapter = TerranAdapter()
    adapter.create_act = MagicMock(side_effect=lambda task, to_count: (task.target, to_count))
    backend._adapter = adapter
    state = snapshot()
    original = asdict(state)
    backend._bridge.snapshot = state
    tasks = [Demand("train", "marine", count=4, produced=1, order_index=0),
             Demand("build", "command_center", order_index=1),
             Demand("train", "marauder", count=2, order_index=2),
             Demand("train", "marine", count=2, order_index=3),
             Demand("build", "barracks", order_index=4),
             Demand("build", "command_center", order_index=5)]
    inactive = Demand("build", "command_center", state=DemandState.COMPLETED, order_index=-1)
    backend.submit([inactive, *reversed(tasks)])
    specs = backend._bridge.macro_specs
    assert [row["task_id"] for row in specs] == [task.task_id for task in tasks]
    assert [row["to_count"] for row in specs] == [8, 7, 2, 10, 3, 8]
    assert [row["factory"]() for row in specs] == [(task.target, absolute) for task, absolute in zip(tasks, [8, 7, 2, 10, 3, 8])]
    assert asdict(state) == original
    assert tasks[0].produced == 1 and all(task.produced == 0 for task in tasks[1:])


def test_submission_delegates_only_owned_count_not_order_prefix():
    backend = SharpyBackend()
    calls = []
    state = BackendSnapshot()
    backend._bridge.snapshot = state
    backend._adapter = NS(production_owned_count=lambda received, action, target: (calls.append((received, action, target)), 10)[1])
    first = Demand("train", "test_only", count=2, order_index=0)
    second = Demand("train", "test_only", count=3, order_index=1)
    backend.submit([second, first])
    assert [row["to_count"] for row in backend._bridge.macro_specs] == [12, 15]
    assert [(action, target) for _received, action, target in calls] == [("train", "test_only")] * 2
    assert all(asdict(received) == asdict(state) for received, _action, _target in calls)


def ability_backend(action, *, orbital=1, ready=0, reason=None, peak=0, failure=None):
    backend = SharpyBackend()
    backend._adapter = TerranAdapter()
    bridge = backend._bridge
    bridge.snapshot = BackendSnapshot(buildings={"orbital_command": orbital},
        info={"scan_ready": ready, "mule_ready": ready})
    bridge.active_task_ids = ["test"]
    bridge.task_meta = {"test": (action, "zone_0" if action == "scan" else "", 1)}
    bridge.peak_ready = {"test": peak}
    if reason:
        bridge.waiting_reasons["test"] = reason
    if failure:
        bridge.macro_errors["test"] = failure
    return backend


@pytest.mark.parametrize("action", ["scan", "call_mule"])
def test_ability_readiness_does_not_credit_cast_or_complete_task(action):
    backend = ability_backend(action, ready=1)
    update = backend.collect_updates()[0]
    assert update.state == DemandState.IN_PROGRESS
    assert update.produced_delta == 0 and update.waiting_for is None
    assert backend._bridge.peak_ready["test"] == 0


@pytest.mark.parametrize("action", ["scan", "call_mule"])
def test_missing_caster_precedes_execution_reason_and_ready_hint(action):
    update = ability_backend(action, orbital=0, ready=1, reason="resources").collect_updates()[0]
    assert update.state == DemandState.WAITING_TO_START
    assert update.waiting_for == "prerequisite:orbital_command"


@pytest.mark.parametrize("action", ["scan", "call_mule"])
def test_acknowledged_cast_completion_does_not_need_readiness_or_adapter(action):
    backend = ability_backend(action, orbital=0, reason="energy", peak=1)
    backend._adapter = None
    update = backend.collect_updates()[0]
    assert update.state == DemandState.COMPLETED and update.waiting_for is None
    assert update.produced_delta == 1
    assert backend.collect_updates()[0].produced_delta == 0


def test_ability_failure_does_not_call_hint_adapter():
    backend = ability_backend("scan", ready=1, failure="controlled cast failure")
    backend._adapter.ability_task_state = MagicMock(side_effect=AssertionError("failure is a shared fact"))
    update = backend.collect_updates()[0]
    assert update.state == DemandState.FAILED
    assert update.failure_reason == "controlled cast failure"
    backend._adapter.ability_task_state.assert_not_called()


def test_feedback_delegates_snapshot_action_and_existing_reason():
    backend = ability_backend("scan", reason="test_reason")
    calls = []
    def describe(action, state, reason):
        calls.append((action, state, reason))
        return DemandState.WAITING_TO_START, "test_adapter_reason"
    backend._adapter = NS(ability_task_state=describe)
    update = backend.collect_updates()[0]
    assert calls == [("scan", backend._bridge.snapshot, "test_reason")]
    assert update.waiting_for == "test_adapter_reason" and update.produced_delta == 0


def test_unimplemented_ability_feedback_does_not_use_terran_defaults():
    with pytest.raises(NotImplementedError, match="ability task feedback"):
        RaceAdapter.ability_task_state(NS(), "scan", BackendSnapshot(), None)
    backend = ability_backend("scan")
    backend._adapter = None
    with pytest.raises(RuntimeError, match="not started"):
        backend.collect_updates()


def test_base_morph_does_not_credit_new_build_output():
    backend = SharpyBackend()
    backend._adapter = TerranAdapter()
    bridge = backend._bridge
    def publish(buildings, tags):
        bridge.on_frame(snapshot=BackendSnapshot(buildings=buildings,
            info={"ready_unit_tags": {}, "building_entity_tags": tags}),
            in_production_units={}, in_progress_buildings={}, in_progress_research={}, macro_errors={})
    publish({"command_center": 1}, {"command_center": [1]})
    task = Demand("build", "command_center")
    backend.submit([task])
    publish({"orbital_command": 1}, {"orbital_command": [1]})
    update = backend.collect_updates()[0]
    assert update.produced_delta == 0 and update.state == DemandState.WAITING_TO_START
    publish({"orbital_command": 1, "command_center": 1}, {"orbital_command": [1], "command_center": [2]})
    update = backend.collect_updates()[0]
    assert update.produced_delta == 1 and update.state == DemandState.COMPLETED


def test_backend_race_rules_have_no_terran_literal_targets():
    from sc2bench_env.backends.sharpy import backend as module
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    forbidden = {"command_center", "orbital_command", "planetary_fortress", "scan_ready", "mule_ready"}
    assert not [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                and isinstance(node.value, str) and node.value in forbidden]
