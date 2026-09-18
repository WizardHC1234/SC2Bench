"""Real SC2 closed loop: build → tech → train → combat → end feedback.

Enable with:
  set SC2BENCH_E2E=1
  pytest tests/e2e/test_closed_loop_e2e.py -q
"""

from __future__ import annotations

import os
from typing import Optional

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


def _combat_ended_event(obs) -> Optional[dict]:
    for event in reversed(list(obs.recent_events or [])):
        if event.get("type") == "combat_ended":
            return dict(event)
    return None


def test_e2e_build_tech_train_combat_end() -> None:
    """First-edition closed loop through end feedback and unit release."""
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        env.reset(
            EpisodeConfig(
                decision_interval_seconds=8.0,
                game_time_limit_seconds=720.0,
                # Protect the tech chain from early random base attacks.
                opponent="builtin_veryeasy",
            )
        )

        # --- build ---
        _build_ready(env, "supply_depot")
        _build_ready(env, "barracks")
        _build_ready(env, "refinery")
        _build_ready(env, "barracks_techlab", max_steps=35)
        env.step([{"action": "build", "target": "supply_depot"}, _wait(6)])

        # --- tech (faster Barracks research keeps the loop practical) ---
        obs, feedback, _, _ = env.step(
            [{"action": "research", "target": "concussive_shells"}, _wait(8)]
        )
        assert feedback.receipts[0].result == "accepted"
        obs, terminated = _wait_until(
            env,
            lambda o: "concussive_shells" in (o.upgrades or [])
            or "concussive_shells" in (o.research or {}),
            max_steps=25,
        )
        assert not terminated, "episode ended before research progressed"
        assert "concussive_shells" in (obs.upgrades or []) or "concussive_shells" in (
            obs.research or {}
        )

        # --- train ---
        env.step(
            [
                {"action": "train", "target": "marine", "count": 3},
                {"action": "train", "target": "marauder", "count": 1},
                _wait(8),
            ]
        )
        obs, terminated = _wait_until(
            env,
            lambda o: o.units.get("marine", 0) >= 2 and o.units.get("marauder", 0) >= 1,
            max_steps=40,
        )
        assert not terminated, "episode ended before army finished"
        assert obs.units.get("marine", 0) >= 2
        assert obs.units.get("marauder", 0) >= 1

        # Finish research if it was still in queue during training.
        if "concussive_shells" not in (obs.upgrades or []):
            obs, terminated = _wait_until(
                env,
                lambda o: "concussive_shells" in (o.upgrades or []),
                max_steps=20,
            )
            assert not terminated
            assert "concussive_shells" in obs.upgrades

        zones = list(obs.zones or [])
        assert zones, "expected zone ids"
        target = zones[min(4, len(zones) - 1)]
        marine_n = min(2, int(obs.own_forces.free.get("marine", obs.units.get("marine", 0))))
        marauder_n = min(1, int(obs.own_forces.free.get("marauder", obs.units.get("marauder", 0))))
        assert marine_n >= 1 and marauder_n >= 1

        # --- combat bind ---
        obs, feedback, _, _ = env.step(
            [
                {
                    "action": "combat",
                    "style": "attack",
                    "target": target,
                    "units": {"marine": marine_n, "marauder": marauder_n},
                },
                _wait(8),
            ]
        )
        assert feedback.receipts[0].result == "accepted"
        assert obs.combat, "combat section should list the active mission"
        assert obs.own_forces.assigned.get("marine", 0) >= 1
        assert obs.own_forces.assigned.get("marauder", 0) >= 1
        assert obs.own_forces.free.get("marine", 0) == max(
            0,
            int(obs.own_forces.army.get("marine", 0))
            - int(obs.own_forces.assigned.get("marine", 0)),
        )

        # --- end feedback ---
        ended = _combat_ended_event(obs)
        for _ in range(55):
            if ended is not None:
                break
            obs, _, terminated, _ = env.step([_wait(8)])
            assert_obs_consistent(obs)
            ended = _combat_ended_event(obs)
            if terminated and ended is None:
                break

        assert ended is not None, (
            "expected combat_ended in recent_events "
            f"(combat={obs.combat!r} events={obs.recent_events!r})"
        )
        assert ended.get("end_reason") in {"force_destroyed", "withdrawn", "cancelled"}
        assert all(
            row.get("end_reason") for name, row in obs.combat.items() if name != "group_0"
        )
        # Bound units must be released after the mission ends.
        assert int(obs.own_forces.assigned.get("marine", 0)) == 0
        assert int(obs.own_forces.assigned.get("marauder", 0)) == 0
        living_marine = int(obs.units.get("marine", 0))
        living_marauder = int(obs.units.get("marauder", 0))
        if living_marine:
            assert int(obs.own_forces.free.get("marine", 0)) == living_marine
        if living_marauder:
            assert int(obs.own_forces.free.get("marauder", 0)) == living_marauder
    finally:
        env.close()
