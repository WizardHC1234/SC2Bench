"""Feedback and Observation must describe the same post-wait boundary."""

from tests.helpers.drive import drive
import pytest

from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig


@pytest.mark.parametrize("action,event_type", [
    ({"name": "train", "arguments": {'target': "scv", 'count': 1}}, "train_completed"),
    ({"name": "build", "arguments": {'target': "supply_depot"}}, "build_started"),
])
def test_feedback_includes_updates_collected_during_this_wait(tmp_path, action, event_type):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=60))
        obs, feedback, _, _ = drive(env, [
            action, {"name": "advance", "arguments": {'seconds': 30}},
        ])
        assert any(event["type"] == event_type for event in feedback.events)
        assert any(event["type"] == event_type for event in obs.recent_events)
        assert all("event_id" in event and "game_time_seconds" in event for event in feedback.events)
        # Previous Feedback owns this turn's events; Recent Events keeps them in
        # the structured record but text rendering hides the duplicate copies.
        feedback_ids = {event["event_id"] for event in feedback.events}
        assert feedback_ids <= {event["event_id"] for event in obs.recent_events}
        recorded = next(row for row in env.trajectory()["steps"] if row["type"] == "step")
        assert recorded["feedback"]["events"] == feedback.to_dict()["events"]
        assert recorded["observation"]["recent_events"] == obs.recent_events
    finally:
        env.close()
