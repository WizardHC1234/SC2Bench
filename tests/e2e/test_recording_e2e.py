"""Real SC2 record/replay artifacts on termination and explicit close."""

from tests.helpers.drive import drive
import json
import os

import pytest

from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


@pytest.mark.parametrize("finish,blocking", [(False, True), (True, True), (True, False)],
                         ids=["explicit_close", "blocking_time_limit", "continuous_time_limit"])
def test_e2e_recording_and_replay(tmp_path, finish, blocking):
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=30.0, blocking_decisions=blocking))
        directory = env.record_path
        obs, feedback, terminated, info = drive(env, [
            {"name": "advance", "arguments": {'seconds': 40 if finish else 3}}
        ])
        assert terminated == finish
        if finish:
            assert obs.terminated and obs.game.game_time_limit_seconds == 30
            assert 30 <= obs.game_time_seconds <= 32
            assert obs.game.seconds_remaining == 0
            assert info["result"] == "Result.Tie" and info["end_reason"] == "time_limit"
        from sc2bench_env.recording.reader import read_episode
        entries = env.trajectory()["steps"]
        assert entries[0]["type"] == "reset"
        step = next(entry for entry in entries if entry["type"] == "step")
        assert step["observation"] == obs.to_dict()
        assert step["feedback"] == feedback.to_dict()
        assert "messages" not in step
        assert step["submitted_decision"][0]["name"] == "advance"
    finally:
        env.close()
    assert (directory / "replay.SC2Replay").stat().st_size > 0
    assert {path.name for path in directory.iterdir()} == {
        "episode.txt", "session.json", "metrics.json", "log.txt", "replay.SC2Replay",
    }
    summary = read_episode(directory)["summary"]
    assert summary["status"] == ("completed" if finish else "interrupted")
    assert summary["end_reason"] == ("time_limit" if finish else "closed_by_caller")
    assert summary["result"] == ("Result.Tie" if finish else None)
    if finish:
        assert summary["game_time_seconds"] == obs.game_time_seconds
        assert env.backend.snapshot().result == "Result.Tie"
        assert env.backend.snapshot().end_reason == "time_limit"


def test_e2e_continuous_game_expires_during_agent_inference_then_closes(tmp_path):
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=30, blocking_decisions=False))
        directory = env.record_path
        assert env.backend._bridge.decision_reached.wait(30)
        snapshot = env.backend.snapshot()
        assert snapshot.terminated and snapshot.result == "Result.Tie"
        assert 30 <= snapshot.game_time_seconds <= 32
    finally:
        env.close()
    from sc2bench_env.recording.reader import read_episode
    summary = read_episode(directory)["summary"]
    assert summary["status"] == "completed" and summary["end_reason"] == "time_limit"
    assert summary["game_time_seconds"] == snapshot.game_time_seconds
    assert summary["decision_count"] == 0
    assert (directory / "replay.SC2Replay").stat().st_size > 0
    entries = env.trajectory()["steps"]
    assert entries[-1]["observation"]["terminated"] is True


@pytest.mark.parametrize("blocking", [True, False], ids=["blocking", "continuous"])
def test_e2e_post_wait_task_events_match_observation_and_record(tmp_path, blocking):
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=45, blocking_decisions=blocking))
        obs, feedback, terminated, info = drive(env, [
            {"name": "train", "arguments": {'target': "scv", 'count': 1}},
            {"name": "advance", "arguments": {'seconds': 60}},
        ])
        assert terminated and info["end_reason"] == "time_limit"
        assert any(event["type"] == "train_completed" for event in feedback.events)
        assert any(event["type"] == "train_completed" for event in obs.recent_events)
        feedback_ids = {event["event_id"] for event in feedback.events}
        assert feedback_ids <= {event["event_id"] for event in obs.recent_events}
        from sc2bench_env.recording.reader import read_episode
        rows = env.trajectory()["steps"]
        step = next(row for row in rows if row["type"] == "step")
        assert step["feedback"]["events"] == feedback.to_dict()["events"]
        assert step["observation"]["recent_events"] == obs.recent_events
    finally:
        env.close()
