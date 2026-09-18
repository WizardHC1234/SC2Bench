"""TaskManager append / cancel / action_id semantics."""

from __future__ import annotations

from sc2bench_env.interface.actions import DecisionBatch, GameAction, WaitAction
from sc2bench_env.runtime.task import DemandState
from sc2bench_env.runtime.task_manager import DemandUpdate, TaskManager


def test_fresh_waiting_update_without_known_blocker_clears_old_reason():
    manager = TaskManager()
    manager.submit([{"action": "build", "target": "factory"}, {"action": "wait"}], game_time=0)
    demand = manager.active_demands()[0]
    manager.apply_updates([DemandUpdate(demand_id=demand.demand_id, action="build", target="factory",
                                        state=DemandState.WAITING_TO_START, waiting_for="resources")],
                          game_time=1)
    assert demand.waiting_for == "resources"
    manager.apply_updates([DemandUpdate(demand_id=demand.demand_id, action="build", target="factory",
                                        state=DemandState.WAITING_TO_START)], game_time=2)
    assert demand.waiting_for is None


def test_build_appends_instead_of_dedup() -> None:
    manager = TaskManager()
    receipts = manager.submit(
        [
            {"action": "build", "target": "barracks"},
            {"action": "build", "target": "barracks"},
            {"action": "wait"},
        ],
        game_time=0.0,
    )
    assert [r.result for r in receipts] == ["accepted", "accepted"]
    assert len(manager.active_demands()) == 2


def test_train_appends_counts() -> None:
    manager = TaskManager()
    manager.submit(
        [
            {"action": "train", "target": "marine", "count": 8},
            {"action": "train", "target": "marine", "count": 4},
            {"action": "wait"},
        ],
        game_time=0.0,
    )
    active = manager.active_demands()
    assert len(active) == 2
    assert sum(d.count for d in active) == 12


def test_action_id_retry_is_ignored() -> None:
    manager = TaskManager()
    first = manager.submit_decision(
        DecisionBatch(
            actions=(GameAction("build", "barracks", 1, action_id="a1"),),
            wait=WaitAction(),
        ),
        game_time=0.0,
    )
    second = manager.submit_decision(
        DecisionBatch(
            actions=(GameAction("build", "barracks", 1, action_id="a1"),),
            wait=WaitAction(),
        ),
        game_time=1.0,
    )
    assert first[0].result == "accepted"
    assert second[0].result == "ignored_duplicate_action_id"
    assert len(manager.active_demands()) == 1


def test_research_is_idempotent() -> None:
    manager = TaskManager()
    first = manager.submit(
        [{"action": "research", "target": "stimpack"}, {"action": "wait"}],
        game_time=0.0,
    )
    second = manager.submit(
        [{"action": "research", "target": "stimpack"}, {"action": "wait"}],
        game_time=1.0,
    )
    assert first[0].result == "accepted"
    assert second[0].result == "idempotent_noop"
    assert len(manager.active_demands()) == 1


def test_research_idempotent_after_queued_completion() -> None:
    """Once research action completes (queued), re-submit stays noop even if upgrade unfinished."""
    manager = TaskManager()
    first = manager.submit(
        [{"action": "research", "target": "stimpack"}, {"action": "wait"}],
        game_time=0.0,
    )
    demand = next(d for d in manager.active_demands() if d.target == "stimpack")
    demand.state = DemandState.COMPLETED
    third = manager.submit(
        [{"action": "research", "target": "stimpack"}, {"action": "wait"}],
        game_time=2.0,
        known_upgrades=set(),
        researching=set(),
    )
    assert first[0].result == "accepted"
    assert third[0].result == "idempotent_noop"
    assert third[0].reason == "research_already_accepted"
    assert not any(
        d.action == "research" and d.target == "stimpack" and d.is_active
        for d in manager.demands.values()
    )


def test_cancel_only_waiting_to_start() -> None:
    manager = TaskManager()
    manager.submit(
        [
            {"action": "build", "target": "barracks"},
            {"action": "build", "target": "factory"},
            {"action": "wait"},
        ],
        game_time=0.0,
    )
    barracks = next(d for d in manager.active_demands() if d.target == "barracks")
    barracks.state = DemandState.WORKER_EN_ROUTE
    receipts = manager.submit(
        [
            {"action": "cancel", "target_action": "build", "target": "barracks"},
            {"action": "cancel", "target_action": "build", "target": "factory"},
            {"action": "wait"},
        ],
        game_time=2.0,
    )
    assert receipts[0].result == "accepted"
    assert "cleared_waiting=0" in (receipts[0].reason or "")
    assert "cleared_waiting=1" in (receipts[1].reason or "")
    assert barracks.state == DemandState.WORKER_EN_ROUTE
    assert not any(d.target == "factory" and d.is_active for d in manager.demands.values())


def test_cancel_then_readd_in_same_batch() -> None:
    manager = TaskManager()
    manager.submit(
        [{"action": "train", "target": "marine", "count": 8}, {"action": "wait"}],
        game_time=0.0,
    )
    manager.submit(
        [
            {"action": "cancel", "target_action": "train", "target": "marine"},
            {"action": "train", "target": "marine", "count": 4},
            {"action": "wait"},
        ],
        game_time=1.0,
    )
    active = manager.active_demands()
    assert len(active) == 1
    assert active[0].count == 4


def test_build_completes_on_entity_appearance_update() -> None:
    manager = TaskManager()
    manager.submit(
        [{"action": "build", "target": "supply_depot"}, {"action": "wait"}],
        game_time=0.0,
    )
    demand = manager.active_demands()[0]
    manager.apply_updates(
        [
            DemandUpdate(
                demand_id=demand.demand_id,
                produced_delta=1,
                state=DemandState.COMPLETED,
            )
        ],
        game_time=5.0,
    )
    assert demand.state == DemandState.COMPLETED
    assert demand.produced == 1
