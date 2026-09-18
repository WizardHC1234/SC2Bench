"""Real SC2 checks for parallel defend/attack ownership.

Enable with:
  set SC2BENCH_E2E=1
  pytest tests/e2e/test_combat_styles_e2e.py -q
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
    assert obs.buildings.get(target, 0) >= 1


def _train_marines(env, count: int = 3) -> None:
    _build_ready(env, "supply_depot")
    _build_ready(env, "barracks")
    env.step([{"action": "build", "target": "supply_depot"}, _wait(6)])
    env.step([{"action": "train", "target": "marine", "count": count}, _wait(8)])
    obs, terminated = _wait_until(
        env, lambda o: o.units.get("marine", 0) >= min(2, count), max_steps=35
    )
    assert not terminated
    assert obs.units.get("marine", 0) >= min(2, count)


def test_e2e_defend_and_attack_intents() -> None:
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
        _train_marines(env, 4)
        obs = env.backend.snapshot()
        zones = list(obs.info.get("zones") or [])
        assert len(zones) >= 2
        home = zones[0]
        far = zones[min(4, len(zones) - 1)]

        living = int(env.backend.snapshot().units.get("marine", 0))
        n = min(2, living)
        assert n >= 1

        obs, feedback, _, _ = env.step(
            [
                {
                    "action": "combat",
                    "style": "defend",
                    "target": home,
                    "units": {"marine": n},
                },
                _wait(8),
            ]
        )
        assert feedback.receipts[0].result == "accepted"
        assert obs.combat
        assert obs.own_forces.assigned.get("marine", 0) >= 1
        assert obs.own_forces.free.get("marine", 0) == max(
            0, int(obs.own_forces.army.get("marine", 0)) - int(obs.own_forces.assigned.get("marine", 0))
        )

        # Separate attack group on a far zone with remaining free marines.
        free_left = int(obs.own_forces.free.get("marine", 0))
        if free_left >= 1:
            m = min(2, free_left)
            obs2, feedback2, _, _ = env.step(
                [
                    {
                        "action": "combat",
                        "style": "attack",
                        "target": far,
                        "units": {"marine": m},
                    },
                    _wait(8),
                ]
            )
            assert feedback2.receipts[0].result == "accepted"
            assert any(row.get("style") == "attack" for row in obs2.combat.values())
            # Bound units must not be free.
            assert obs2.own_forces.assigned.get("marine", 0) >= n
        else:
            # All marines already on defend — still a valid style acceptance.
            assert any(row.get("style") == "defend" for row in obs.combat.values())
    finally:
        env.close()
