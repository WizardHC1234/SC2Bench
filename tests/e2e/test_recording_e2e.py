"""Real SC2 record/replay artifacts on termination and explicit close."""

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
        obs, feedback, terminated, info = env.step([
            {"action": "wait", "any_of": [{"condition": "interval", "seconds": 40 if finish else 3}]}
        ])
        assert terminated == finish
        if finish:
            assert obs.terminated and obs.game.game_time_limit_seconds == 30
            assert 30 <= obs.game_time_seconds <= 32
            assert obs.game.seconds_remaining == 0
            assert info["result"] == "Result.Tie" and info["end_reason"] == "time_limit"
        from sc2bench_env.recording.reader import read_episode
        record = read_episode(directory)
        entries = record["steps"]
        assert entries[0]["type"] == "reset"
        step = next(entry for entry in entries if entry["type"] == "step")
        assert step["observation"] == obs.to_dict()
        assert step["feedback"] == feedback.to_dict()
        assert "messages" not in step
        interactions = record
        assert interactions["interactions"][0]["output"]["submitted_decision"][0]["action"] == "wait"
        assert interactions["interactions"][0]["step_index"] == step["step_index"]
        assert "environment_response" not in interactions["interactions"][0]
    finally:
        env.close()
    assert (directory / "replay.SC2Replay").stat().st_size > 0
    assert {path.name for path in directory.iterdir()} == {
        "episode.txt", "interactions.jsonl", "replay.SC2Replay",
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
    entries = read_episode(directory)["steps"]
    assert entries[-1]["observation"]["terminated"] is True


@pytest.mark.parametrize("blocking", [True, False], ids=["blocking", "continuous"])
def test_e2e_post_wait_task_events_match_observation_and_record(tmp_path, blocking):
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=45, blocking_decisions=blocking))
        obs, feedback, terminated, info = env.step([
            {"action": "train", "target": "scv", "count": 1},
            {"action": "wait", "any_of": [{"condition": "interval", "seconds": 60}]},
        ])
        assert terminated and info["end_reason"] == "time_limit"
        assert any(event["type"] == "train_completed" for event in obs.recent_events)
        assert feedback.events == obs.recent_events
        from sc2bench_env.recording.reader import read_episode
        rows = read_episode(env.record_path)["steps"]
        step = next(row for row in rows if row["type"] == "step")
        assert step["feedback"]["events"] == step["observation"]["recent_events"]
    finally:
        env.close()
