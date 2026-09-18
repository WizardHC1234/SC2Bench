"""Verify both decision modes in the actual non-realtime SC2 runner."""

import os
import time

import pytest

from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


@pytest.mark.parametrize("blocking", [True, False])
def test_e2e_decision_boundary_modes(tmp_path, blocking):
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        obs = env.reset(EpisodeConfig(
            blocking_decisions=blocking, game_time_limit_seconds=180,
            decision_interval_seconds=3, opponent="builtin_easy",
        ))
        initial = obs.game_time_seconds
        time.sleep(0.6)  # Stand in for external model latency, with no API call.
        current = env.backend.snapshot().game_time_seconds
        assert current == initial if blocking else current > initial
        obs, _, done, _ = env.step([
            {"action": "train", "target": "scv", "count": 1},
            {"action": "wait", "any_of": [{"condition": "interval", "seconds": 3}]},
        ])
        assert not done
        boundary = obs.game_time_seconds
        time.sleep(0.6)
        current = env.backend.snapshot().game_time_seconds
        assert current == boundary if blocking else current > boundary
        obs, feedback, _, _ = env.step(None)
        assert feedback.events[0]["type"] == "decision_rejected"
        time.sleep(0.6)
        if blocking:
            assert obs.game_time_seconds == env.backend.snapshot().game_time_seconds == boundary
        obs, _, done, _ = env.step([
            {"action": "wait", "any_of": [{"condition": "interval", "seconds": 3}]},
        ])
        assert not done and obs.game_time_seconds > boundary
        worker = env.backend._thread
        record_path = env.record_path
    finally:
        env.close()
    assert not worker.is_alive()
    assert (record_path / "replay.SC2Replay").stat().st_size > 0

