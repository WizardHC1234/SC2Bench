"""Cancellation facts mirror execution boundaries without prescribing strategy."""

from tests.helpers.drive import submit_calls
import copy

import pytest

from sc2bench_env.catalog.registry import render_system_prompt
from sc2bench_env.interface.observations import render_observation_text
from sc2bench_env.runtime.task import DemandState as S
from sc2bench_env.runtime.task_manager import TaskManager
from sc2bench_env.interface.tools import tool_specs, validate_tool_arguments


@pytest.mark.parametrize("action,target,state,produced,queued,count,expected", [
    ("build", "barracks", S.WAITING_TO_START, 0, None, 1, 1),
    ("build", "barracks", S.WORKER_EN_ROUTE, 0, None, 1, 0),
    ("build", "barracks", S.UNDER_CONSTRUCTION, 0, None, 1, 0),
    ("research", "stimpack", S.WAITING_TO_START, 0, None, 1, 1),
    ("research", "stimpack", S.IN_PROGRESS, 0, None, 1, 0),
    ("train", "marine", S.WAITING_TO_START, 0, None, 8, 8),
    ("train", "marine", S.IN_PRODUCTION, 2, 3, 8, 3),
    ("train", "marine", S.IN_PRODUCTION, 2, 6, 8, 0),
    ("train", "marine", S.WAITING_TO_START, 2, 0, 8, 6),
    ("train", "marine", S.IN_PRODUCTION, 0, None, 8, None),
    ("train", "marine", S.IN_PRODUCTION, 2, None, 8, None),
])
def test_reported_cancellable_counts_match_known_cancel_boundary(
        action, target, state, produced, queued, count, expected):
    manager = TaskManager()
    command = {"name": action, "arguments": {"target": target}}
    if action == "train":
        command["arguments"]["count"] = count
    submit_calls(manager, [command, {"name": "advance", "arguments": {'seconds': 5}}], game_time=0)
    demand = manager.active_demands()[0]
    demand.state, demand.produced, demand.in_flight = state, produced, queued
    before = copy.deepcopy(demand)
    rows = manager.production_priority_summary()
    assert rows[0]["cancellable_count"] == expected
    text = render_observation_text({"production_priority": rows})
    assert "task_id" not in rows[0]
    assert demand.demand_id not in text
    if type(expected) is int and expected > 0:
        assert f"cancellable {expected}" in text
    else:
        assert "cancellable" not in text
    assert demand == before
    if expected is not None:
        receipt = submit_calls(manager, [
            {"name": "cancel", "arguments": {'target_action': action, 'target': target}},
            {"name": "advance", "arguments": {'seconds': 5}}], game_time=1)[0]
        assert receipt.reason == f"cleared_waiting={expected}"


def test_cancel_all_matching_rounds_then_readd_at_tail_preserves_other_work():
    manager = TaskManager()
    for count in (8, 4):
        submit_calls(manager, [{"name": "train", "arguments": {'target': "marine", 'count': count}},
                        {"name": "advance", "arguments": {'seconds': 5}}], game_time=count)
    submit_calls(manager, [{"name": "build", "arguments": {'target': "factory"}},
                    {"name": "advance", "arguments": {'seconds': 5}}], game_time=9)
    first = manager.active_demands()[0]
    first.state, first.produced, first.in_flight = S.IN_PRODUCTION, 2, 3
    assert sum(row["cancellable_count"] for row in
               manager.production_priority_summary() if row["target"] == "marine") == 7
    receipts = submit_calls(manager, [
        {"name": "cancel", "arguments": {'target_action': "train", 'target': "marine"}},
        {"name": "train", "arguments": {'target': "marine", 'count': 2}},
        {"name": "advance", "arguments": {'seconds': 5}}], game_time=10)
    assert receipts[0].reason == "cleared_waiting=7"
    assert first.count == 5 and first.produced == 2 and first.in_flight == 3
    assert [(row["target"], row["cancellable_count"]) for row in
            manager.production_priority_summary()] == [("marine", 0), ("factory", 1), ("marine", 2)]


def test_cancel_clears_all_matching_unstarted_requests():
    manager = TaskManager()
    submit_calls(manager, [
        {"name": "train", "arguments": {"target": "marine", "count": 8}},
        {"name": "train", "arguments": {"target": "marine", "count": 4}},
        {"name": "advance", "arguments": {"seconds": 5}},
    ], game_time=0)
    receipt = submit_calls(manager, [
        {"name": "cancel", "arguments": {"target_action": "train", "target": "marine"}},
        {"name": "advance", "arguments": {"seconds": 5}},
    ], game_time=1)[0]
    assert receipt.reason == "cleared_waiting=12"
    assert manager.production_priority_summary() == []


def test_cancel_count_trims_newest_matching_work_first():
    manager = TaskManager()
    submit_calls(manager, [
        {"name": "train", "arguments": {"target": "marine", "count": 8}},
        {"name": "train", "arguments": {"target": "marine", "count": 4}},
        {"name": "advance", "arguments": {"seconds": 5}},
    ], game_time=0)

    receipt = submit_calls(manager, [
        {"name": "cancel", "arguments": {
            "target_action": "train", "target": "marine", "count": 6,
        }},
        {"name": "advance", "arguments": {"seconds": 5}},
    ], game_time=1)[0]

    assert receipt.reason == "cleared_waiting=6"
    assert receipt.count == 6
    assert receipt.details == {
        "removed_unstarted": 6,
        "remaining_unstarted": 6,
        "protected_started_or_paid": 0,
        "requested_count": 6,
        "unremoved_count": 0,
    }
    rows = manager.production_priority_summary()
    assert [(row["target"], row["remaining"]) for row in rows] == [
        ("marine", 6),
    ]


def test_cancel_count_reports_partial_effect_and_protects_paid_work():
    manager = TaskManager()
    submit_calls(manager, [
        {"name": "train", "arguments": {"target": "marine", "count": 8}},
        {"name": "advance", "arguments": {"seconds": 5}},
    ], game_time=0)
    demand = manager.active_demands()[0]
    demand.state, demand.produced, demand.in_flight = S.IN_PRODUCTION, 2, 3

    receipt = submit_calls(manager, [
        {"name": "cancel", "arguments": {
            "target_action": "train", "target": "marine", "count": 5,
        }},
        {"name": "advance", "arguments": {"seconds": 5}},
    ], game_time=1)[0]

    assert receipt.reason == "cleared_waiting=3"
    assert receipt.details == {
        "removed_unstarted": 3,
        "remaining_unstarted": 0,
        "protected_started_or_paid": 3,
        "requested_count": 5,
        "unremoved_count": 2,
    }
    assert demand.count == 5


def test_prompt_explains_revision_without_mandating_cancellation():
    prompt = render_system_prompt()
    assert "cancel edits the queue by removing matching unstarted work" in prompt
    assert "must cancel" not in prompt.lower()
    from sc2bench_env.interface.platform_rules import ACTION_RULES
    assert "unstarted" in ACTION_RULES["cancel"]
    assert "without a refund" in ACTION_RULES["cancel"]


def test_old_observation_missing_cancellable_count_is_not_inferred():
    data = {"production_priority": [{"action": "build", "target": "barracks",
                                     "state": "waiting_to_start", "remaining": 1}]}
    before = copy.deepcopy(data)
    text = render_observation_text(data)
    assert "1. build barracks; remaining 1" in text
    assert "Status: not started; waiting: unknown" in text
    assert "cancellable" not in text
    assert data == before


def test_cancel_tool_count_is_optional_and_positive():
    spec = next(item for item in tool_specs("terran") if item.name == "cancel")
    assert "count" in spec.parameters["properties"]
    assert "count" not in spec.parameters["required"]
    assert validate_tool_arguments(spec, {
        "target_action": "train", "target": "marine", "count": 2,
    }) is None
    assert validate_tool_arguments(spec, {
        "target_action": "train", "target": "marine", "count": 0,
    }) == "count must be at least 1"
