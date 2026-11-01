"""Minimal live checks for the shared three-race action interface.

Enable with SC2BENCH_E2E=1. Debug cheats only speed construction and grant resources.
"""
from __future__ import annotations

import os

import pytest

from tests.e2e.test_base_abilities_e2e import _install, _wait
from tests.helpers.drive import drive
from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def _step(env, actions, state, seconds=1):
    obs, feedback, terminated, _ = drive(env, [*actions, _wait(seconds)])
    assert not terminated, obs.game
    assert_obs_consistent(obs)
    state["last_feedback"] = feedback
    return obs, feedback


def _until(env, state, predicate, limit=20):
    obs = None
    for _ in range(limit):
        obs, _ = _step(env, [], state)
        if predicate(obs):
            return obs
    raise AssertionError(getattr(obs, "building", None) if obs is not None else "no observation")


def _cheat(monkeypatch):
    state = {}

    async def collect(act):
        if state.get("resources") and not state.get("filled"):
            await act.ai.client.debug_all_resources()
            state["filled"] = True
        if state.get("fast") and not state.get("sped"):
            await act.ai.client.debug_fast_build()
            state["sped"] = True

    _install(monkeypatch, collect)
    return state


def _townhall(obs):
    return next(row for row in obs.structures if str(row.get("id", "")).startswith("townhall_"))


def test_e2e_terran_townhall_morph(monkeypatch) -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig
    from sc2bench_env.interface.tools import action_tool_names

    assert "morph_townhall" in action_tool_names("terran")
    assert "upgrade" not in action_tool_names("terran")
    state = _cheat(monkeypatch)
    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(
            race="terran", enemy_race="zerg", opponent="veryeasy", game_time_limit_seconds=240,
        ))
        hall = _townhall(obs)
        assert hall["id"] == "townhall_0"
        assert hall["type"] == "command_center"
        assert not any(str(row["id"]).startswith("cc_") for row in obs.structures)
        state["resources"] = True
        state["fast"] = True
        obs, feedback = _step(env, [
            {"name": "build", "arguments": {"target": "supply_depot"}},
            {"name": "build", "arguments": {"target": "barracks"}},
        ], state, 1)
        assert feedback.receipts[0].result == "accepted"
        obs = _until(env, state, lambda item: int(item.buildings.get("barracks", 0) or 0) >= 1, limit=24)
        obs, feedback = _step(env, [{
            "name": "morph_townhall",
            "arguments": {"target": "townhall_0", "to": "orbital_command"},
        }], state, 1)
        assert feedback.receipts[0].result == "accepted"
        obs = _until(env, state, lambda item: int(item.orbital_count or 0) >= 1, limit=24)
        morphed = _townhall(obs)
        assert morphed["id"] == "townhall_0"
        assert morphed["type"] == "orbital_command"
    finally:
        env.close()


def test_e2e_zerg_larva_batch_and_lair(monkeypatch) -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    state = _cheat(monkeypatch)
    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(
            race="zerg", enemy_race="terran", opponent="veryeasy", game_time_limit_seconds=240,
        ))
        assert _townhall(obs)["type"] == "hatchery"
        assert "morph_townhall" in {spec.name for spec in env.tool_specs()}
        state["resources"] = True
        state["fast"] = True
        obs, _ = _step(env, [{"name": "build", "arguments": {"target": "spawning_pool"}}], state, 1)
        obs = _until(env, state, lambda item: int(item.buildings.get("spawning_pool", 0) or 0) >= 1)
        before = int(obs.units.get("zergling", 0) or 0)
        obs, feedback = _step(env, [{"name": "train", "arguments": {"target": "zergling", "count": 1}}], state, 1)
        assert feedback.receipts[0].result == "accepted"
        obs = _until(
            env, state,
            lambda item: int(item.units.get("zergling", 0) or 0) >= before + 2,
            limit=24,
        )
        completed = [row for row in obs.recent_events if row.get("type") == "train_completed" and row.get("target") == "zergling"]
        assert completed, obs.recent_events
        assert completed[-1]["requested_count"] == 1
        assert completed[-1]["actual_produced"] >= 2
        assert completed[-1]["mechanism"] == "larva"
        obs, feedback = _step(env, [{
            "name": "morph_townhall",
            "arguments": {"target": "townhall_0", "to": "lair"},
        }], state, 1)
        assert feedback.receipts[0].result == "accepted"
        obs = _until(
            env, state,
            lambda item: int((item.building.get("lair") or {}).get("completed") or 0) >= 1
            or any(row.get("id") == "townhall_0" and row.get("type") == "lair" for row in item.structures),
            limit=24,
        )
        assert any(row.get("id") == "townhall_0" for row in obs.structures)
    finally:
        env.close()


def test_e2e_protoss_has_no_townhall_morph(monkeypatch) -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig
    from sc2bench_env.interface.tools import action_tool_names

    assert "morph_townhall" not in action_tool_names("protoss")
    assert "chrono_boost" in action_tool_names("protoss")
    state = _cheat(monkeypatch)
    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(
            race="protoss", enemy_race="terran", opponent="veryeasy", game_time_limit_seconds=90,
        ))
        hall = _townhall(obs)
        assert hall["id"] == "townhall_0"
        assert hall["type"] == "nexus"
        names = {spec.name for spec in env.tool_specs()}
        assert "morph_townhall" not in names
        assert "upgrade" not in names
        obs, feedback = _step(env, [{
            "name": "morph_townhall",
            "arguments": {"target": "townhall_0", "to": "lair"},
        }], state, 1)
        assert any(row.get("type") == "decision_rejected" for row in feedback.events)
        assert _townhall(obs)["type"] == "nexus"
    finally:
        env.close()
