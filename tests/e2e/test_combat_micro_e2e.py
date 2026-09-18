"""Real SC2 checks for combat micro: banshee cloak and medivac drop phases.

Enable with:
  set SC2BENCH_E2E=1
  pytest tests/e2e/test_combat_micro_e2e.py -q
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


def _build_ready(env, target: str, *, max_steps: int = 30) -> None:
    env.step([{"action": "build", "target": target}, _wait(8)])
    obs, terminated = _wait_until(
        env, lambda o: o.buildings.get(target, 0) >= 1, max_steps=max_steps
    )
    assert not terminated, f"episode ended before {target} finished"
    assert obs.buildings.get(target, 0) >= 1, f"{target} not ready"


def _enemy_ish_zone(obs) -> str:
    zones = list(obs.zones or [])
    assert zones, "expected zones"
    return zones[min(4, len(zones) - 1)]


def test_e2e_banshee_cloak_on_attack() -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        env.reset(
            EpisodeConfig(
                decision_interval_seconds=8.0,
                game_time_limit_seconds=600.0,
                # Isolate skill execution from random attacks at our start;
                # an immediate, valid task retreat is not a cloak regression.
                opponent="builtin_veryeasy",
            )
        )
        _build_ready(env, "supply_depot")
        _build_ready(env, "barracks")
        _build_ready(env, "refinery")
        _build_ready(env, "factory", max_steps=35)
        _build_ready(env, "starport", max_steps=35)
        _build_ready(env, "starport_techlab", max_steps=40)
        env.step([{"action": "research", "target": "cloaking_field"}, _wait(8)])
        env.step([{"action": "build", "target": "supply_depot"}, _wait(6)])
        env.step([{"action": "train", "target": "banshee", "count": 1}, _wait(8)])
        obs, terminated = _wait_until(
            env,
            lambda o: o.units.get("banshee", 0) >= 1
            and "cloaking_field" in (o.upgrades or []),
            max_steps=45,
        )
        assert not terminated
        assert env.backend.snapshot().units.get("banshee", 0) >= 1
        assert "cloaking_field" in obs.upgrades

        target = _enemy_ish_zone(obs)
        obs, feedback, _, _ = env.step(
            [
                {
                    "action": "combat",
                    "style": "attack",
                    "target": target,
                    "units": {"banshee": 1},
                },
                _wait(8),
            ]
        )
        assert feedback.receipts[0].result == "accepted"
        assert obs.combat

        cloaked = any(row.get("cloaked", {}).get("banshee", 0) > 0 for row in obs.combat.values())
        for _ in range(8):
            obs, _, terminated, _ = env.step([_wait(6)])
            assert_obs_consistent(obs)
            if terminated:
                break
            if any(row.get("cloaked", {}).get("banshee", 0) > 0 for row in obs.combat.values()):
                cloaked = True
                break
        assert cloaked, "expected actual cloaked Banshee, not merely an active mission"
    finally:
        env.close()


def test_e2e_medivac_drop_phases() -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        env.reset(
            EpisodeConfig(
                decision_interval_seconds=8.0,
                game_time_limit_seconds=600.0,
                opponent="builtin_easy",
            )
        )
        _build_ready(env, "supply_depot")
        _build_ready(env, "barracks")
        _build_ready(env, "refinery")
        _build_ready(env, "factory", max_steps=35)
        _build_ready(env, "starport", max_steps=35)
        env.step([{"action": "build", "target": "supply_depot"}, _wait(6)])
        env.step(
            [
                {"action": "train", "target": "marine", "count": 4},
                {"action": "train", "target": "medivac", "count": 1},
                _wait(8),
            ]
        )
        obs, terminated = _wait_until(
            env,
            lambda o: o.units.get("marine", 0) >= 2 and o.units.get("medivac", 0) >= 1,
            max_steps=45,
        )
        assert not terminated
        marine_n = min(2, int(obs.units.get("marine", 0)))
        target = _enemy_ish_zone(obs)
        obs, feedback, _, _ = env.step(
            [
                {
                    "action": "combat",
                    "style": "attack",
                    "target": target,
                    "units": {"marine": marine_n, "medivac": 1},
                },
                _wait(8),
            ]
        )
        assert feedback.receipts[0].result == "accepted"

        saw_transport_phase = any(row.get("transport", {}).get("peak_loaded_units", 0) > 0
                                  for row in obs.combat.values())
        saw_alive_passengers = False
        saw_unload = False
        for _ in range(10):
            obs, _, terminated, _ = env.step([_wait(6)])
            assert_obs_consistent(obs)
            if terminated:
                break
            if not obs.combat:
                continue
            row = next(iter(obs.combat.values()))
            transport = row.get("transport") or {}
            if transport.get("peak_loaded_units", 0) > 0:
                saw_transport_phase = True
            if transport.get("drop_unloaded"):
                saw_unload = True
            alive = row.get("alive") or {}
            if int(alive.get("marine", 0)) >= 1 and int(alive.get("medivac", 0)) >= 1:
                saw_alive_passengers = True
            if saw_transport_phase and saw_alive_passengers and saw_unload:
                break

        assert saw_transport_phase, "expected actual passengers from backend-decided attack transport"
        assert saw_alive_passengers, "passengers/medivac must stay in combat alive counts"
        assert saw_unload, "expected actual unloading, not only a phase label"
    finally:
        env.close()
