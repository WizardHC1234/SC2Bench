"""Optional real SC2 smoke tests for the corrected Phase 0/1 contract.

Enable with:
  set SC2BENCH_E2E=1
  pytest tests/e2e -q
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


def test_e2e_build_starts_then_world_completes() -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        obs = env.reset(
            EpisodeConfig(
                decision_interval_seconds=8.0,
                game_time_limit_seconds=180.0,
                opponent="builtin_easy",
            )
        )
        assert_obs_consistent(obs)
        assert obs.zones
        assert env.get_system_prompt()

        obs, feedback, terminated, _ = env.step(
            [{"action": "build", "target": "supply_depot"}, _wait(8)]
        )
        assert feedback.receipts[0].result == "accepted"
        assert not terminated

        saw_under = False
        action_finished = False
        for _ in range(20):
            obs, terminated = _wait_until(
                env,
                lambda o: True,
                max_steps=1,
            )
            depot = obs.building.get("supply_depot", {})
            if depot.get("under_construction", 0) >= 1 or depot.get("completed", 0) >= 1:
                saw_under = True
            active = [
                d
                for d in env.task_manager.active_demands()
                if d.action == "build" and d.target == "supply_depot"
            ]
            if saw_under and not active:
                action_finished = True
                break
            if obs.buildings.get("supply_depot", 0) >= 1 and not active:
                action_finished = True
                break
            if terminated:
                break

        assert saw_under or obs.buildings.get("supply_depot", 0) >= 1
        assert action_finished or not any(
            d.action == "build" and d.target == "supply_depot" and d.is_active
            for d in env.task_manager.demands.values()
        )

        for _ in range(20):
            obs, terminated = _wait_until(
                env, lambda o: o.buildings.get("supply_depot", 0) >= 1, max_steps=1
            )
            if obs.buildings.get("supply_depot", 0) >= 1:
                break
            if terminated:
                break
        assert obs.buildings.get("supply_depot", 0) >= 1
        assert obs.building["supply_depot"]["completed"] >= 1
    finally:
        env.close()


def test_e2e_train_marine_with_wait() -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        env.reset(
            EpisodeConfig(
                decision_interval_seconds=8.0,
                game_time_limit_seconds=300.0,
                opponent="builtin_easy",
            )
        )
        env.step([{"action": "build", "target": "supply_depot"}, _wait(8)])
        obs, terminated = _wait_until(
            env, lambda o: o.buildings.get("supply_depot", 0) >= 1, max_steps=20
        )
        assert not terminated

        env.step([{"action": "build", "target": "barracks"}, _wait(8)])
        obs, terminated = _wait_until(
            env, lambda o: o.buildings.get("barracks", 0) >= 1, max_steps=25
        )
        assert obs.buildings.get("barracks", 0) >= 1

        env.step([{"action": "train", "target": "marine", "count": 3}, _wait(8)])
        obs, terminated = _wait_until(env, lambda o: o.units.get("marine", 0) >= 3, max_steps=30)
        assert obs.units.get("marine", 0) >= 3
        assert obs.training.get("marine", {}).get("living", 0) >= 3
    finally:
        env.close()


def test_e2e_cancel_waiting_build() -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        env.reset(
            EpisodeConfig(
                decision_interval_seconds=5.0,
                game_time_limit_seconds=120.0,
                opponent="builtin_easy",
            )
        )
        # Barracks without depot should remain waiting_to_start.
        env.step([{"action": "build", "target": "barracks"}, _wait(5)])
        obs, terminated = _wait_until(
            env,
            lambda o: o.building.get("barracks", {}).get("waiting_to_start", 0) >= 1,
            max_steps=6,
        )
        assert not terminated
        assert obs.building.get("barracks", {}).get("waiting_to_start", 0) >= 1

        obs, feedback, _, _ = env.step(
            [
                {"action": "cancel", "target_action": "build", "target": "barracks"},
                _wait(5),
            ]
        )
        assert feedback.receipts[0].result == "accepted"
        obs, _ = _wait_until(env, lambda o: True, max_steps=1)
        assert obs.building.get("barracks", {}).get("waiting_to_start", 0) == 0
        assert not any(
            d.action == "build" and d.target == "barracks" and d.is_active
            for d in env.task_manager.demands.values()
        )
    finally:
        env.close()


def test_e2e_build_command_center_expand() -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        obs = env.reset(
            EpisodeConfig(
                decision_interval_seconds=8.0,
                game_time_limit_seconds=240.0,
                opponent="builtin_easy",
            )
        )
        assert obs.base_count == 1

        env.step([{"action": "build", "target": "supply_depot"}, _wait(8)])
        obs, terminated = _wait_until(
            env,
            lambda o: o.buildings.get("supply_depot", 0) >= 1 and o.resources.minerals >= 400,
            max_steps=20,
        )
        assert not terminated

        env.step([{"action": "build", "target": "command_center"}, _wait(8)])
        obs, terminated = _wait_until(env, lambda o: o.base_count >= 2, max_steps=30)
        assert obs.base_count >= 2
        townhalls = (
            obs.buildings.get("command_center", 0)
            + obs.buildings.get("orbital_command", 0)
            + obs.buildings.get("planetary_fortress", 0)
        )
        assert townhalls == obs.base_count
    finally:
        env.close()


def test_e2e_upgrade_orbital_then_scan() -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        obs = env.reset(
            EpisodeConfig(
                decision_interval_seconds=10.0,
                game_time_limit_seconds=420.0,
                opponent="builtin_easy",
            )
        )
        assert obs.structures
        cc_id = next(row["id"] for row in obs.structures if row["type"] == "command_center")

        env.step([{"action": "build", "target": "supply_depot"}, _wait(10)])
        obs, terminated = _wait_until(
            env, lambda o: o.buildings.get("supply_depot", 0) >= 1, max_steps=15
        )
        assert not terminated

        env.step([{"action": "build", "target": "barracks"}, _wait(10)])
        obs, terminated = _wait_until(
            env, lambda o: o.buildings.get("barracks", 0) >= 1, max_steps=25
        )
        assert obs.buildings.get("barracks", 0) >= 1

        obs, terminated = _wait_until(
            env, lambda o: o.resources.minerals >= 150, max_steps=20
        )
        obs, feedback, _, _ = env.step(
            [{"action": "upgrade", "target": cc_id, "to": "orbital_command"}, _wait(10)]
        )
        assert feedback.receipts[0].result == "accepted"

        obs, terminated = _wait_until(env, lambda o: o.orbital_count >= 1, max_steps=30)
        assert obs.orbital_count >= 1
        assert any(row["id"] == cc_id and row["type"] == "orbital_command" for row in obs.structures)

        obs, terminated = _wait_until(env, lambda o: o.scan_ready >= 1, max_steps=40)
        assert obs.scan_ready >= 1
        zone = obs.zones[0]
        obs, feedback, _, _ = env.step([{"action": "scan", "target": zone}, _wait(8)])
        assert feedback.receipts[0].result == "accepted"
        for _ in range(10):
            obs, terminated = _wait_until(env, lambda o: True, max_steps=1)
            if not any(
                d.action == "scan" and d.is_active for d in env.task_manager.demands.values()
            ):
                break
            if terminated:
                break
        assert not any(
            d.action == "scan" and d.is_active for d in env.task_manager.demands.values()
        )
    finally:
        env.close()


def test_e2e_call_mule_and_scout() -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        obs = env.reset(
            EpisodeConfig(
                decision_interval_seconds=10.0,
                game_time_limit_seconds=480.0,
                opponent="builtin_easy",
            )
        )
        cc_id = next(row["id"] for row in obs.structures if row["type"] == "command_center")
        home = obs.zones[0]

        env.step([{"action": "build", "target": "supply_depot"}, _wait(10)])
        obs, terminated = _wait_until(
            env, lambda o: o.buildings.get("supply_depot", 0) >= 1, max_steps=15
        )
        assert not terminated
        env.step([{"action": "build", "target": "barracks"}, _wait(10)])
        obs, terminated = _wait_until(
            env, lambda o: o.buildings.get("barracks", 0) >= 1, max_steps=25
        )
        assert obs.buildings.get("barracks", 0) >= 1

        env.step(
            [{"action": "upgrade", "target": cc_id, "to": "orbital_command"}, _wait(10)]
        )
        obs, terminated = _wait_until(env, lambda o: o.orbital_count >= 1, max_steps=30)
        assert obs.orbital_count >= 1

        # Scout own base first (short route), then wait for mule energy.
        obs, feedback, _, _ = env.step(
            [{"action": "scout", "route": [home]}, _wait(8)]
        )
        assert feedback.receipts[0].result == "accepted"
        for _ in range(20):
            obs, terminated = _wait_until(env, lambda o: True, max_steps=1)
            if not any(
                d.action == "scout" and d.is_active for d in env.task_manager.demands.values()
            ):
                break
            if terminated:
                break
        assert not any(
            d.action == "scout" and d.is_active for d in env.task_manager.demands.values()
        )

        obs, terminated = _wait_until(
            env, lambda o: o.mule_ready >= 1 or o.scan_ready >= 1, max_steps=45
        )
        assert obs.mule_ready >= 1 or obs.scan_ready >= 1

        obs, feedback, _, _ = env.step([{"action": "call_mule"}, _wait(8)])
        assert feedback.receipts[0].result == "accepted"
        for _ in range(12):
            obs, terminated = _wait_until(env, lambda o: True, max_steps=1)
            if not any(
                d.action == "call_mule" and d.is_active
                for d in env.task_manager.demands.values()
            ):
                break
            if terminated:
                break
        assert not any(
            d.action == "call_mule" and d.is_active for d in env.task_manager.demands.values()
        )
    finally:
        env.close()


def test_e2e_research_stimpack_queued() -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        env.reset(
            EpisodeConfig(
                decision_interval_seconds=10.0,
                game_time_limit_seconds=540.0,
                opponent="builtin_easy",
            )
        )

        env.step([{"action": "build", "target": "supply_depot"}, _wait(10)])
        obs, terminated = _wait_until(
            env, lambda o: o.buildings.get("supply_depot", 0) >= 1, max_steps=12
        )
        assert not terminated

        env.step([{"action": "build", "target": "barracks"}, _wait(10)])
        obs, terminated = _wait_until(
            env, lambda o: o.buildings.get("barracks", 0) >= 1, max_steps=20
        )
        assert obs.buildings.get("barracks", 0) >= 1

        env.step([{"action": "build", "target": "refinery"}, _wait(10)])
        obs, terminated = _wait_until(
            env, lambda o: o.buildings.get("refinery", 0) >= 1, max_steps=20
        )
        assert obs.buildings.get("refinery", 0) >= 1

        env.step([{"action": "train", "target": "scv", "count": 6}, _wait(10)])
        obs, terminated = _wait_until(env, lambda o: o.resources.vespene >= 25, max_steps=25)
        assert obs.resources.vespene >= 25

        env.step([{"action": "build", "target": "barracks_techlab"}, _wait(10)])
        obs, terminated = _wait_until(
            env, lambda o: o.buildings.get("barracks_techlab", 0) >= 1, max_steps=25
        )
        assert obs.buildings.get("barracks_techlab", 0) >= 1

        obs, terminated = _wait_until(
            env,
            lambda o: o.resources.vespene >= 100 and o.resources.minerals >= 100,
            max_steps=25,
        )
        obs, feedback, _, _ = env.step(
            [{"action": "research", "target": "stimpack"}, _wait(10)]
        )
        assert feedback.receipts[0].result == "accepted"

        for _ in range(25):
            obs, terminated = _wait_until(env, lambda o: True, max_steps=1)
            active = [
                d
                for d in env.task_manager.active_demands()
                if d.action == "research" and d.target == "stimpack"
            ]
            if not active and (
                obs.research.get("stimpack") in {"in_progress", "completed"}
                or "stimpack" in obs.upgrades
            ):
                break
            if terminated:
                break

        assert not any(
            d.action == "research" and d.target == "stimpack" and d.is_active
            for d in env.task_manager.demands.values()
        ), "research action should end once queued"
        assert obs.research.get("stimpack") in {"in_progress", "completed"} or "stimpack" in obs.upgrades

        # Idempotent after accept/queue.
        obs, feedback, _, _ = env.step(
            [{"action": "research", "target": "stimpack"}, _wait(5)]
        )
        assert feedback.receipts[0].result == "idempotent_noop"
    finally:
        env.close()
