"""Real SC2 production checks for first-wave Terran army units.

Costs / tech chains aligned with
SC2-Commander/evol_agent/sc2_data_agent/data_sc2_260701.

Enable with:
  set SC2BENCH_E2E=1
  pytest tests/e2e/test_train_army_e2e.py -q
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


def _produced(env, target: str) -> int:
    return sum(
        int(d.produced)
        for d in env.task_manager.demands.values()
        if d.action == "train" and d.target == target
    )


def _build_ready(env, target: str, *, max_steps: int = 25) -> None:
    env.step([{"action": "build", "target": target}, _wait(8)])
    obs, terminated = _wait_until(
        env, lambda o: o.buildings.get(target, 0) >= 1, max_steps=max_steps
    )
    assert not terminated, f"episode ended before {target} finished"
    assert obs.buildings.get(target, 0) >= 1, f"{target} not ready"


def test_e2e_train_marauder() -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        env.reset(
            EpisodeConfig(
                decision_interval_seconds=8.0,
                game_time_limit_seconds=480.0,
                opponent="builtin_veryeasy",
            )
        )
        _build_ready(env, "supply_depot")
        _build_ready(env, "barracks")
        _build_ready(env, "refinery")
        _build_ready(env, "barracks_techlab", max_steps=30)
        _wait_until(
            env,
            lambda o: o.resources.vespene >= 25 and o.resources.minerals >= 100,
            max_steps=25,
        )
        env.step([{"action": "build", "target": "supply_depot"}, _wait(6)])
        obs, feedback, _, _ = env.step(
            [{"action": "train", "target": "marauder", "count": 1}, _wait(8)]
        )
        assert feedback.receipts[0].result == "accepted"
        obs, terminated = _wait_until(
            env,
            lambda o: _produced(env, "marauder") >= 1 or o.units.get("marauder", 0) >= 1,
            max_steps=30,
        )
        assert _produced(env, "marauder") >= 1 or obs.units.get("marauder", 0) >= 1
        assert not terminated
    finally:
        env.close()


def test_e2e_train_siege_tank_medivac_banshee() -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        env.reset(
            EpisodeConfig(
                decision_interval_seconds=8.0,
                game_time_limit_seconds=600.0,
                opponent="builtin_veryeasy",
            )
        )
        _build_ready(env, "supply_depot")
        _build_ready(env, "barracks")
        _build_ready(env, "refinery")
        _build_ready(env, "factory", max_steps=30)
        env.step([{"action": "build", "target": "refinery"}, _wait(8)])
        _wait_until(env, lambda o: o.buildings.get("refinery", 0) >= 2, max_steps=20)
        _build_ready(env, "factory_techlab", max_steps=30)
        _build_ready(env, "starport", max_steps=30)
        _build_ready(env, "starport_techlab", max_steps=30)
        env.step([{"action": "build", "target": "supply_depot"}, _wait(6)])
        env.step([{"action": "build", "target": "supply_depot"}, _wait(6)])
        _wait_until(
            env,
            lambda o: o.resources.minerals >= 400 and o.resources.vespene >= 325,
            max_steps=40,
        )

        obs, feedback, _, _ = env.step(
            [
                {"action": "train", "target": "siege_tank", "count": 1},
                {"action": "train", "target": "medivac", "count": 1},
                {"action": "train", "target": "banshee", "count": 1},
                _wait(10),
            ]
        )
        assert [r.result for r in feedback.receipts] == ["accepted", "accepted", "accepted"]

        # Production success is units leaving the queue (demand.produced), not
        # surviving the rest of the episode against the builtin AI.
        obs, terminated = _wait_until(
            env,
            lambda o: (
                _produced(env, "siege_tank") >= 1
                and _produced(env, "medivac") >= 1
                and _produced(env, "banshee") >= 1
            ),
            max_steps=45,
        )
        assert _produced(env, "siege_tank") >= 1
        assert _produced(env, "medivac") >= 1
        assert _produced(env, "banshee") >= 1
    finally:
        env.close()
