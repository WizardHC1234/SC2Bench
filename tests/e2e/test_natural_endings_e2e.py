"""Natural defeat through normal AI combat, never leave/debug/time-limit.

The external test Agent deliberately builds no defense. This checks ending
plumbing, not a useful policy, win rate, or active battle-strategy quality.
"""

import json
import os

import pytest

from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


@pytest.mark.parametrize("blocking", [True, False], ids=["blocking", "continuous"])
def test_e2e_natural_defeat_preserves_result_and_real_interactions(tmp_path, blocking):
    env = Environment("sharpy", record_dir=tmp_path)
    rounds = 0
    try:
        observation = env.reset(EpisodeConfig(
            opponent="builtin_veryhard", game_time_limit_seconds=1440,
            blocking_decisions=blocking,
        ))
        directory = env.record_path
        terminated = False
        while not terminated and rounds < 30:
            decision = [{"action": "wait", "any_of": [
                {"condition": "interval", "seconds": 60},
            ]}]
            if rounds == 0:
                # Leave an intentionally unfinished supply-blocked demand.
                # A lost match must not turn its unproduced units into credit.
                decision.insert(0, {"action": "train", "target": "scv", "count": 20})
            observation, feedback, terminated, info = env.step(
                decision, agent_context={
                    "messages": env.get_context(),
                    "assistant_content": json.dumps(decision, separators=(",", ":")),
                    "model": "natural_ending_test_agent",
                },
            )
            rounds += 1
            assert_obs_consistent(observation)
            assert not any(e.get("type") == "decision_rejected" for e in feedback.events)
        assert terminated and observation.terminated
        assert info["result"] == "Result.Defeat"
        assert info["end_reason"] == "game_ended"
        assert observation.game_time_seconds < 1440
        demand = next(d for d in env.task_manager.demands.values()
                      if d.action == "train" and d.target == "scv")
        assert 0 < demand.produced < demand.count
        assert demand.remaining == demand.count - demand.produced
        summary_before_close = (directory / "episode.txt").read_bytes()
        terminal_observation = observation.to_dict()
    finally:
        env.close()

    # Closing a finished game must not change Defeat to caller interruption.
    assert (directory / "episode.txt").read_bytes() == summary_before_close
    from sc2bench_env.recording.reader import read_episode
    record = read_episode(directory)
    summary = record["summary"]
    assert summary["status"] == "completed" and summary["error"] is None
    assert summary["result"] == "Result.Defeat" and summary["end_reason"] == "game_ended"
    assert summary["game_time_seconds"] == observation.game_time_seconds
    assert summary["decision_count"] == rounds and summary["rejected_count"] == 0
    assert env.backend.snapshot().result == summary["result"]
    assert env.backend.snapshot().end_reason == summary["end_reason"]
    entries = record["steps"]
    steps = [entry for entry in entries if entry["type"] == "step"]
    ends = [entry for entry in entries if entry["type"] == "end"]
    assert len(steps) == rounds and len(ends) == 1
    assert steps[-1]["terminated"] and steps[-1]["observation"] == terminal_observation
    assert steps[-1]["info"]["result"] == summary["result"]
    assert steps[-1]["feedback"] == feedback.to_dict()
    interactions = record
    assert len(interactions["interactions"]) == rounds
    assert all(row["input"]["messages"] and row["output"]["assistant_content"]
               for row in interactions["interactions"])
    assert (directory / "replay.SC2Replay").stat().st_size > 0
