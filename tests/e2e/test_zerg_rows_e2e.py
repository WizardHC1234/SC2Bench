"""One live Zerg game for larva rows, overlord supply, and no auto-rebuild.

Enable with SC2BENCH_E2E=1.
"""
from __future__ import annotations

from tests.helpers.drive import drive
from sc2bench_env.interface.observations import observation_lines


import os

import pytest

from tests.e2e.test_base_abilities_e2e import _install, _wait
from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def _step(env, actions, state, seconds=1):
    obs, feedback, terminated, _ = drive(env, [*actions, _wait(seconds)])
    assert not terminated, obs.game
    assert_obs_consistent(obs)
    return obs, feedback


def _until(env, state, predicate, limit=20):
    obs = None
    for _ in range(limit):
        obs, _ = _step(env, [], state)
        if predicate(obs):
            return obs
    raise AssertionError(obs.building if obs is not None else "no observation")


def _accepted(obs, facility):
    lines = observation_lines(obs)
    for index, line in enumerate(lines):
        if line == f"{facility}:":
            return lines[index + 1]
    raise AssertionError(facility)


def test_e2e_zerg_macro_rows(monkeypatch) -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    state = {}

    async def collect(act):
        if state.get("kill_pool") and not state.get("killed"):
            from sc2.ids.unit_typeid import UnitTypeId
            tags = [unit.tag for unit in act.ai.structures(UnitTypeId.SPAWNINGPOOL)]
            if tags:
                await act.ai.client.debug_kill_unit(tags)
            state["killed"] = True
        if state.get("resources") and not state.get("filled"):
            await act.ai.client.debug_all_resources()
            state["filled"] = True
        if state.get("want_fast") and not state.get("is_fast"):
            await act.ai.client.debug_fast_build()
            state["is_fast"] = True
        if not state.get("want_fast") and state.get("is_fast"):
            await act.ai.client.debug_fast_build()
            state["is_fast"] = False

    _install(monkeypatch, collect)
    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(
            race="zerg", enemy_race="terran", opponent="veryeasy", game_time_limit_seconds=180,
        ))
        opening_cap = int(obs.resources.supply_cap)
        state["resources"] = True
        obs, _ = _step(env, [{"name": "train", "arguments": {"target": "overlord", "count": 1}}], state, 1)
        assert int(obs.resources.supply_cap) == opening_cap
        state["want_fast"] = True
        obs = _until(env, state, lambda item: int(item.units.get("overlord", 0) or 0) >= 2)
        assert int(obs.resources.supply_cap) >= opening_cap + 8

        obs, _ = _step(env, [{"name": "build", "arguments": {"target": "spawning_pool"}}], state, 1)
        obs = _until(env, state, lambda item: int(item.buildings.get("spawning_pool", 0) or 0) >= 1)
        state["want_fast"] = False
        obs, _ = _step(env, [
            {"name": "train", "arguments": {"target": "zergling", "count": 4}},
            {"name": "train", "arguments": {"target": "queen", "count": 1}},
        ], state, 1)
        larva = _accepted(obs, "larva")
        hatchery = _accepted(obs, "hatchery")
        assert "zergling" in larva
        assert "queen" in hatchery
        assert "unknown" not in larva and "unknown" not in hatchery

        state["kill_pool"] = True
        obs, _ = _step(env, [], state, 2)
        assert int(obs.buildings.get("spawning_pool", 0) or 0) == 0
        obs, _ = _step(env, [], state, 2)
        assert int(obs.buildings.get("spawning_pool", 0) or 0) == 0
    finally:
        env.close()
