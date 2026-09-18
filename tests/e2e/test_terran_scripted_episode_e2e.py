"""Long Terran episode through the public interface and an external Agent.

This is a stability diagnostic, not evidence of model quality or benchmark
performance. Run with SC2BENCH_E2E=1.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.helpers.terran_baseline import TerranBaselineAgent
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


@pytest.mark.parametrize("blocking", [True, False], ids=["blocking", "continuous"])
def test_terran_scripted_agent_survives_real_battles_until_terminal(tmp_path, blocking):
    agent = TerranBaselineAgent()
    env = Environment("sharpy", record_dir=tmp_path)
    rounds = 0
    terminated = False
    directory = None
    contact_frames = 0
    assigned_frames = 0
    try:
        observation = env.reset(EpisodeConfig(
            opponent="builtin_veryeasy",
            decision_interval_seconds=15,
            game_time_limit_seconds=900,
            blocking_decisions=blocking,
        ))
        directory = env.record_path
        assert directory is not None
        assert_obs_consistent(observation)

        while not terminated and rounds < 80:
            public_state = observation.to_dict()
            messages = env.get_context()
            decision = agent.decide(public_state)
            observation, feedback, terminated, info = env.step(
                decision,
                agent_context={
                    "messages": messages,
                    "assistant_content": json.dumps(decision, separators=(",", ":")),
                    "model": "scripted_terran_baseline",
                },
            )
            rounds += 1
            assert_obs_consistent(observation)
            assert not any(event.get("type") == "decision_rejected"
                           for event in feedback.events)
            assert not any(receipt.result == "rejected" for receipt in feedback.receipts)
            assigned_frames += int(any(row.get("assigned") for row in observation.combat.values()))
            contact_frames += int(any(
                row.get("zone_role") in {"enemy_main", "enemy_natural"}
                and row["own_contents"]["units"].get("marine", 0) > 0
                and any(row["visible_enemy_contents"][group] for group in ("units", "buildings"))
                for row in observation.zone_state
            ))

        assert terminated, "public Agent loop did not reach an episode terminal state"
        assert assigned_frames > 0 and contact_frames > 0, "commands alone are not battle evidence"
        missions = [d for d in env.task_manager.demands.values() if d.action == "combat"]
        destroyed = [d for d in missions if d.end_reason == "force_destroyed"]
        if info["result"] != "Result.Victory":
            assert len(destroyed) >= 1 and len(missions) >= 2, "no loss/rebind cycle was exercised"
        assert all(not d.is_active for d in destroyed)
        produced = sum(d.produced for d in env.task_manager.demands.values()
                       if d.action == "train" and d.target == "marine")
        # Actual living stock plus completely destroyed groups cannot exceed
        # credited births; casualties must not erase already-produced progress.
        destroyed_count = sum((d.units or {}).get("marine", 0) for d in destroyed)
        assert produced >= observation.own_forces.army.get("marine", 0) + destroyed_count
        terminal_observation = observation.to_dict()
        summary_before_close = (directory / "episode.txt").read_bytes()
    finally:
        env.close()

    assert directory is not None
    from sc2bench_env.recording.reader import read_episode
    record = read_episode(directory)
    summary = record["summary"]
    assert summary["status"] == "completed"
    assert summary["end_reason"] in {"game_ended", "time_limit"}
    assert summary["result"] == info["result"]
    assert summary["end_reason"] == info["end_reason"]
    assert summary["game_time_seconds"] == observation.game_time_seconds
    assert (directory / "episode.txt").read_bytes() == summary_before_close
    if summary["end_reason"] == "time_limit":
        assert summary["result"] == "Result.Tie"
        assert 900 <= summary["game_time_seconds"] <= 902
    entries = record["steps"]
    steps = [entry for entry in entries if entry["type"] == "step"]
    assert len(steps) == rounds and sum(entry["type"] == "end" for entry in entries) == 1
    assert steps[-1]["terminated"] and steps[-1]["observation"] == terminal_observation
    assert steps[-1]["feedback"] == feedback.to_dict()
    assert summary["decision_count"] == rounds and summary["rejected_count"] == 0
    interactions = record
    assert len(interactions["interactions"]) == rounds
    assert all(row["input"]["messages"] and row["output"]["assistant_content"]
               for row in interactions["interactions"])
    assert (directory / "replay.SC2Replay").stat().st_size > 0
