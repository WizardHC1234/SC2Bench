"""Deep real-SC2 scout checks: multi-zone route, scouting obs, live replace.

Enable with:
  set SC2BENCH_E2E=1
  pytest tests/e2e/test_scout_e2e.py -q
"""

from __future__ import annotations

import os

import pytest

from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def _wait(seconds: float = 4.0) -> dict:
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


def test_e2e_automatic_expansion_route_and_manual_replace() -> None:
    """Real route expansion/assignment/progress; not all-map success evidence."""
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy", record_trajectory=False)
    try:
        initial = env.reset(EpisodeConfig(opponent="builtin_easy",
                           decision_interval_seconds=4, game_time_limit_seconds=90))
        own = {row["zone_id"] for row in initial.zone_state if row["known_owner"] == "self"}
        obs, feedback, terminated, _ = env.step([
            {"action": "scout", "route": "all"}, _wait(4)])
        assert feedback.receipts[0].result == "accepted"
        assert not terminated
        row = obs.scouting["scv"]
        assert row["mode"] == "all_expansions"
        assert set(row["route"]) == set(initial.zones) - own
        assert len(row["route"]) == len(set(row["route"]))
        assert row["assigned"] and row["moving_to"] in row["route"]
        assert "all_expansions" in "\n".join(obs.section_lines())
        route = list(row["route"])
        obs, _, terminated, _ = env.step([_wait(20)])
        assert not terminated and obs.scouting["scv"]["route"] == route
        assert obs.scouting["scv"]["waypoint_index"] > 0
        obs, feedback, terminated, _ = env.step([
            {"action": "scout", "route": [route[0]]}, _wait(1)])
        assert feedback.receipts[0].result == "accepted"
        assert not terminated
        active = [d for d in env.task_manager.active_demands() if d.action == "scout"]
        assert len(active) == 1 and active[0].route == (route[0],)
        assert any(d.route == "all" and d.state.value == "cancelled"
                   for d in env.task_manager.demands.values())
    finally:
        env.close()


def test_e2e_scout_multi_zone_and_replace() -> None:
    """Multi-zone route, scouting obs, and live route replacement on real SC2."""
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        obs = env.reset(
            EpisodeConfig(
                decision_interval_seconds=4.0,
                game_time_limit_seconds=300.0,
                opponent="builtin_easy",
            )
        )
        assert_obs_consistent(obs)
        assert len(obs.zones) >= 3
        home = obs.zones[0]
        # Prefer non-home expansions so the SCV must leave the mineral line.
        far_a = obs.zones[1]
        far_b = obs.zones[2]
        route = [far_a, far_b]

        obs, feedback, _, _ = env.step(
            [{"action": "scout", "route": route}, _wait(3)]
        )
        assert feedback.receipts[0].result == "accepted"
        assert "scv" in obs.scouting
        assert obs.scouting["scv"]["route"] == route

        saw_assigned = False
        saw_moving = False
        for _ in range(40):
            obs, terminated = _wait_until(env, lambda o: True, max_steps=1)
            row = obs.scouting.get("scv") or {}
            if row.get("assigned"):
                saw_assigned = True
            moving = row.get("moving_to")
            if moving in set(route):
                saw_moving = True
            if not row:
                break
            if terminated:
                break

        assert saw_assigned, "scout SCV was never assigned"
        assert saw_moving, f"scouting.moving_to never entered route {route}"

        # Start a long unfinished route, then replace it mid-flight.
        long_route = [far_b, far_a, home]
        obs, feedback, _, _ = env.step(
            [{"action": "scout", "route": long_route}, _wait(3)]
        )
        assert feedback.receipts[0].result == "accepted"
        assert obs.scouting.get("scv", {}).get("route") == long_route

        for _ in range(8):
            obs, terminated = _wait_until(env, lambda o: True, max_steps=1)
            row = obs.scouting.get("scv") or {}
            if row.get("assigned") or row.get("moving_to"):
                break
            if terminated:
                break
        assert any(
            d.action == "scout" and d.is_active for d in env.task_manager.demands.values()
        ), "long scout route should still be active before replace"

        short_route = [far_a]
        before_ids = {
            d.demand_id
            for d in env.task_manager.demands.values()
            if d.action == "scout"
        }
        obs, feedback, _, _ = env.step(
            [{"action": "scout", "route": short_route}, _wait(3)]
        )
        assert feedback.receipts[0].result == "accepted"
        cancelled = [
            d
            for d in env.task_manager.demands.values()
            if d.action == "scout" and d.state.value == "cancelled"
        ]
        assert cancelled, "previous unfinished scout must be cancelled on replace"
        assert any(list(d.route or ()) == long_route for d in cancelled)

        replacement = [
            d
            for d in env.task_manager.demands.values()
            if d.action == "scout"
            and d.demand_id not in before_ids
            and list(d.route or ()) == short_route
        ]
        assert replacement, "replacement scout demand missing"
        # May still be running or already finished inside the wait window.
        assert replacement[0].state.value in {
            "in_progress",
            "completed",
            "waiting_to_start",
        }
        active = [d for d in env.task_manager.active_demands() if d.action == "scout"]
        if active:
            assert list(active[0].route or ()) == short_route
            assert obs.scouting.get("scv", {}).get("route") == short_route

        for _ in range(30):
            obs, terminated = _wait_until(env, lambda o: True, max_steps=1)
            if not any(
                d.action == "scout" and d.is_active for d in env.task_manager.demands.values()
            ):
                break
            if terminated:
                break
        assert not any(
            d.action == "scout" and d.is_active for d in env.task_manager.demands.values()
        ), "replacement scout route should finish"
        assert replacement[0].state.value == "completed" or not replacement[0].is_active
    finally:
        env.close()
