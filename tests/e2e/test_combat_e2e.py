"""Real SC2 check: bind idle Marines and drive a combat mission.

Enable with:
  set SC2BENCH_E2E=1
  pytest tests/e2e/test_combat_e2e.py -q
"""

from __future__ import annotations

import os

import pytest

from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def _wait(seconds: float = 8.0) -> dict:
    return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}


def _wait_until(env, pred, *, max_steps: int = 40):
    obs = None
    terminated = False
    for _ in range(max_steps):
        obs, _, terminated, _ = env.step([_wait()])
        assert_obs_consistent(obs)
        if pred(obs) or terminated:
            return obs, terminated
    return obs, terminated


def _build_ready(env, target: str, *, max_steps: int = 25) -> None:
    env.step([{"action": "build", "target": target}, _wait(8)])
    obs, terminated = _wait_until(
        env, lambda o: o.buildings.get(target, 0) >= 1, max_steps=max_steps
    )
    assert not terminated, f"episode ended before {target} finished"
    assert obs.buildings.get(target, 0) >= 1, f"{target} not ready"


def test_e2e_combat_attack_marines() -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        env.reset(
            EpisodeConfig(
                decision_interval_seconds=8.0,
                game_time_limit_seconds=480.0,
                opponent="builtin_easy",
            )
        )
        _build_ready(env, "supply_depot")
        _build_ready(env, "barracks")
        env.step([{"action": "build", "target": "supply_depot"}, _wait(6)])
        env.step([{"action": "train", "target": "marine", "count": 3}, _wait(8)])
        obs, terminated = _wait_until(
            env, lambda o: o.units.get("marine", 0) >= 2, max_steps=30
        )
        assert not terminated, "episode ended before marines finished"
        assert obs.units.get("marine", 0) >= 2

        zones = list(obs.zones or [])
        assert zones, "expected zone ids in observation"
        # Prefer a non-home zone when available.
        target_zone = zones[min(3, len(zones) - 1)]

        # Insufficient request must fail immediately.
        _, feedback_bad, _, _ = env.step(
            [
                {
                    "action": "combat",
                    "style": "attack",
                    "target": target_zone,
                    "units": {"marine": 99},
                },
                _wait(2),
            ]
        )
        assert feedback_bad.receipts[0].result == "rejected"
        assert feedback_bad.receipts[0].reason == "insufficient_units"

        marine_count = min(2, int(obs.units.get("marine", 0)))
        obs, feedback, _, _ = env.step(
            [
                {
                    "action": "combat",
                    "style": "attack",
                    "target": target_zone,
                    "units": {"marine": marine_count},
                },
                _wait(8),
            ]
        )
        assert feedback.receipts[0].result == "accepted"
        assert obs.combat, "combat section should list the active mission"
        row = next(iter(obs.combat.values()))
        assert row["style"] == "attack"
        assert row["target"] == target_zone
        assert int(row["alive"].get("marine", 0)) >= 1
        assert row["assigned"] is True

        # Second wait: mission should still be active or ended objectively.
        obs2, _, _, _ = env.step([_wait(8)])
        assert_obs_consistent(obs2)
        still_active = any(
            row.get("style") == "attack" and row.get("target") == target_zone
            for row in obs2.combat.values()
        )
        ended = any(
            event.get("type") == "combat_ended" and event.get("end_reason") == "force_destroyed"
            for event in obs2.recent_events
        )
        assert still_active or ended
    finally:
        env.close()
