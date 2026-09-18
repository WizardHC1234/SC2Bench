"""Feedback and Observation must describe the same post-wait boundary."""

import pytest

from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig


@pytest.mark.parametrize("action,event_type", [
    ({"action": "train", "target": "scv", "count": 1}, "train_completed"),
    ({"action": "build", "target": "supply_depot"}, "build_started"),
])
def test_feedback_includes_updates_collected_during_this_wait(tmp_path, action, event_type):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=60))
        obs, feedback, _, _ = env.step([
            action, {"action": "wait", "any_of": [{"condition": "interval", "seconds": 30}]},
        ])
        assert any(event["type"] == event_type for event in obs.recent_events)
        assert feedback.events == obs.recent_events
        recorded = next(row for row in env.trajectory()["steps"] if row["type"] == "step")
        assert recorded["feedback"]["events"] == recorded["observation"]["recent_events"]
    finally:
        env.close()
