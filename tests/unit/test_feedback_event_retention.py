"""A burst of production changes must not hide earlier failures from the agent."""
import pytest

from sc2bench_env.adapters.llm.observation import platform_messages
from sc2bench_env.interface.actions import AdvanceAction, DecisionBatch, GameAction
from sc2bench_env.runtime.task_manager import DemandUpdate, TaskManager


def changes(race="terran", target="marine"):
    manager = TaskManager(race=race)
    manager.submit_decision(DecisionBatch(
        actions=tuple(GameAction("train", target, 1) for _ in range(21)),
        advance=AdvanceAction(5),
    ), game_time=0)
    manager.begin_decision_step(game_time=5)
    demands = manager.active_demands()
    manager.apply_updates([
        DemandUpdate(demand_id=demands[0].demand_id, failure_reason="producer_destroyed"),
        *(DemandUpdate(demand_id=d.demand_id, produced_delta=1) for d in demands[1:]),
    ], game_time=10)
    return manager


@pytest.mark.parametrize("race,target", [
    ("terran", "marine"), ("protoss", "zealot"), ("zerg", "roach"),
])
def test_production_burst_preserves_failure_and_all_completions_in_model_text(race, target):
    manager = changes(race, target)
    events = manager.events_since_boundary()
    assert len(events) == 21
    assert events[0]["type"] == "demand_failed"
    assert len(manager.recent_events) == manager.event_limit
    assert all(d.is_active is False for d in manager.demands.values())
    text = platform_messages("", {}, {"events": events})[-1]["content"]
    assert "producer_destroyed" in text
    for event in events:
        assert f"[#{event['event_id']} @" in text


def test_feedback_survives_savepoint_and_next_boundary_keeps_history_bounded():
    manager = changes()
    saved = manager.export_state()
    expected = manager.events_since_boundary()
    manager.begin_decision_step(game_time=15)
    assert manager.events_since_boundary() == []
    manager.import_state(saved)
    assert manager.events_since_boundary() == expected
    manager.events_since_boundary().clear()
    assert manager.events_since_boundary() == expected
    manager.begin_decision_step(game_time=15)
    assert manager.events_since_boundary() == []
    assert len(manager.recent_events) == manager.event_limit
    manager.reset()
    assert manager.events_since_boundary() == []


def test_older_savepoint_without_boundary_buffer_can_be_restored():
    manager = changes()
    saved = manager.export_state()
    saved.pop("boundary_events")
    restored = TaskManager()
    restored.import_state(saved)
    assert restored.events_since_boundary() == manager.recent_events
