"""Cancellation facts mirror execution boundaries without prescribing strategy."""

import copy

import pytest

from sc2bench_env.interface.action_catalog import render_system_prompt
from sc2bench_env.interface.observations import render_observation_text
from sc2bench_env.runtime.task import DemandState as S
from sc2bench_env.runtime.task_manager import TaskManager


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
    command = {"action": action, "target": target}
    if action == "train":
        command["count"] = count
    manager.submit([command, {"action": "wait"}], game_time=0)
    demand = manager.active_demands()[0]
    demand.state, demand.produced, demand.in_flight = state, produced, queued
    before = copy.deepcopy(demand)
    rows = manager.production_priority_summary()
    assert rows[0]["cancellable_count"] == expected
    text = render_observation_text({"production_priority": rows})
    assert "Cancellable" in text
    line = next(line for line in text.splitlines() if line.startswith("1 | "))
    assert line.endswith(" | " + ("unknown" if expected is None else str(expected)))
    assert demand == before
    if expected is not None:
        receipt = manager.submit([
            {"action": "cancel", "target_action": action, "target": target},
            {"action": "wait"}], game_time=1)[0]
        assert receipt.reason == f"cleared_waiting={expected}"


def test_cancel_all_matching_rounds_then_readd_at_tail_preserves_other_work():
    manager = TaskManager()
    for count in (8, 4):
        manager.submit([{"action": "train", "target": "marine", "count": count},
                        {"action": "wait"}], game_time=count)
    manager.submit([{"action": "build", "target": "factory"},
                    {"action": "wait"}], game_time=9)
    first = manager.active_demands()[0]
    first.state, first.produced, first.in_flight = S.IN_PRODUCTION, 2, 3
    assert sum(row["cancellable_count"] for row in
               manager.production_priority_summary() if row["target"] == "marine") == 7
    receipts = manager.submit([
        {"action": "cancel", "target_action": "train", "target": "marine"},
        {"action": "train", "target": "marine", "count": 2},
        {"action": "wait"}], game_time=10)
    assert receipts[0].reason == "cleared_waiting=7"
    assert first.count == 5 and first.produced == 2 and first.in_flight == 3
    assert [(row["target"], row["cancellable_count"]) for row in
            manager.production_priority_summary()] == [("marine", 0), ("factory", 1), ("marine", 2)]


def test_prompt_explains_revision_without_mandating_cancellation():
    prompt = render_system_prompt()
    for phrase in ("ALL matching", "across requests/rounds", "no count or order ID",
                   "before backend execution", "appends at the tail", "if unwanted",
                   "paid queued units", "active research stay"):
        assert phrase in prompt
    assert "must cancel" not in prompt.lower()


def test_old_observation_missing_cancellable_count_is_not_inferred():
    data = {"production_priority": [{"action": "build", "target": "barracks",
                                     "state": "waiting_to_start", "remaining": 1}]}
    before = copy.deepcopy(data)
    text = render_observation_text(data)
    assert next(line for line in text.splitlines() if line.startswith("1 | ")).endswith(" | unknown")
    assert data == before
