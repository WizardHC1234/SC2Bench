"""One live Terran game for the macro rows still marked unit or open.

Fast build only shortens construction after the mining and cost checks.
Enable with SC2BENCH_E2E=1.
"""
from __future__ import annotations


from tests.helpers.drive import drive
import os

import pytest

from sc2bench_env.interface.tools import action_tool_names
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


def _until(env, state, predicate, limit=12):
    obs = None
    for _ in range(limit):
        obs, _ = _step(env, [], state)
        if predicate(obs):
            return obs
    raise AssertionError(obs.building if obs is not None else "no observation")


def test_e2e_terran_macro_rows(monkeypatch) -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    terran = set(action_tool_names("terran"))
    protoss = set(action_tool_names("protoss"))
    zerg = set(action_tool_names("zerg"))
    assert {"scan", "call_mule", "morph_townhall"} <= terran
    assert "chrono_boost" in protoss
    assert {"scan", "call_mule", "morph_townhall"}.isdisjoint(protoss)
    assert {"inject_larva", "spawn_creep_tumor", "morph_townhall"} <= zerg
    assert {"scan", "call_mule"}.isdisjoint(zerg)

    state = {}

    async def collect(act):
        if state.get("clear_army") and not state.get("cleared"):
            tags = [unit.tag for unit in act.ai.enemy_units if not getattr(unit, "is_structure", False)]
            if tags:
                await act.ai.client.debug_kill_unit(tags)
            state["cleared"] = True
        if state.get("resources") and not state.get("filled"):
            await act.ai.client.debug_all_resources()
            state["filled"] = True
        if state.get("fast") and not state.get("sped"):
            await act.ai.client.debug_fast_build()
            state["sped"] = True

    _install(monkeypatch, collect)
    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(
            race="terran", enemy_race="zerg", opponent="veryeasy", game_time_limit_seconds=180,
        ))
        assert_obs_consistent(obs)
        opening_cap = int(obs.resources.supply_cap)
        opening_minerals = int(obs.resources.minerals)
        assert sum(obs.own_forces.workers.values()) >= 8
        main = next(row["zone_id"] for row in obs.zone_state if row.get("zone_role") == "own_main")
        field = next(row for row in obs.map_control.base_resources if row["zone_id"] == main)
        opening_field = int(field["minerals_remaining"])
        mined = None
        for _ in range(8):
            obs, _ = _step(env, [], state, 2)
            field = next(row for row in obs.map_control.base_resources if row["zone_id"] == main)
            if int(obs.resources.minerals) > opening_minerals or int(field["minerals_remaining"]) < opening_field:
                mined = obs
                break
        assert mined is not None
        assert int(obs.resources.supply_cap) == opening_cap

        state["resources"] = True
        obs, _ = _step(env, [{"name": "build", "arguments": {"target": "factory"}}], state, 2)
        paid = int(obs.resources.minerals)
        factory = obs.building.get("factory", {})
        assert str(factory.get("waiting_for", "")).startswith("prerequisite")
        assert int(factory.get("completed", 0) or 0) == 0
        obs, _ = _step(env, [], state, 2)
        assert int(obs.resources.minerals) >= paid - 50

        obs, _ = _step(env, [
            {"name": "build", "arguments": {"target": "supply_depot"}},
            {"name": "build", "arguments": {"target": "barracks"}},
        ], state, 1)
        assert int(obs.resources.supply_cap) == opening_cap
        state["fast"] = True
        obs = _until(
            env, state,
            lambda item: int(item.buildings.get("supply_depot", 0) or 0) >= 1
            and int(item.buildings.get("barracks", 0) or 0) >= 1,
        )
        assert int(obs.resources.supply_cap) >= opening_cap + 8

        obs, _ = _step(env, [
            {"name": "build", "arguments": {"target": "barracks_techlab"}},
            {"name": "train", "arguments": {"target": "marine", "count": 2}},
        ], state, 1)
        obs = _until(env, state, lambda item: int(item.buildings.get("barracks_techlab", 0) or 0) >= 1)
        assert "marauder" in obs.available_targets.get("train", [])
        obs, feedback = _step(env, [
            {"name": "build", "arguments": {"target": "barracks_techlab"}},
        ], state, 1)
        assert obs.building.get("barracks_techlab", {}).get("waiting_for") == "addon_host_unavailable"

        cc = next(row["id"] for row in obs.structures if str(row.get("id", "")).startswith("townhall_"))
        before_scan = int(obs.scan_ready or 0)
        obs, _ = _step(env, [
            {"name": "morph_townhall", "arguments": {"target": cc, "to": "orbital_command"}},
        ], state, 1)
        obs = _until(env, state, lambda item: int(item.orbital_count or 0) >= 1, limit=16)
        obs, _ = _step(env, [], state, 3)
        assert int(obs.scan_ready or 0) >= before_scan
        assert int(obs.buildings.get("orbital_command", 0) or 0) >= 1

        obs = _until(env, state, lambda item: int(item.units.get("marine", 0) or 0) >= 1, limit=16)
        obs, _ = _step(env, [{"name": "build", "arguments": {"target": "bunker"}}], state, 1)
        obs = _until(env, state, lambda item: int(item.buildings.get("bunker", 0) or 0) >= 1, limit=16)
        obs = _until(
            env, state,
            lambda item: sum(item.own_forces.bunker_garrison.values()) >= 1,
            limit=8,
        )
        free = int(obs.own_forces.free.get("marine", 0) or 0)
        obs, feedback = _step(env, [{
            "name": "combat",
            "arguments": {"style": "attack", "target": "zone_0", "units": {"marine": free + 5}},
        }], state, 1)
        assert feedback.receipts[0].result == "rejected"
        assert feedback.receipts[0].reason == "insufficient_units"

        state["clear_army"] = True
        obs, _ = _step(env, [], state, 2)
        assert not obs.terminated
    finally:
        env.close()
