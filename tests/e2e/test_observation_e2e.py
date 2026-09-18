"""Real SC2 checks for core Observation sections."""

from __future__ import annotations

import os

import pytest

from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def _wait(seconds: float = 6.0) -> dict:
    return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}


def _wait_until(env, pred, *, max_steps: int = 20):
    obs = None
    terminated = False
    for _ in range(max_steps):
        obs, _, terminated, _ = env.step([_wait()])
        assert_obs_consistent(obs)
        if pred(obs) or terminated:
            return obs, terminated
    return obs, terminated


def test_e2e_observation_core_sections() -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        obs = env.reset(
            EpisodeConfig(
                decision_interval_seconds=6.0,
                game_time_limit_seconds=180.0,
                opponent="builtin_easy",
            )
        )
        assert_obs_consistent(obs)
        assert obs.game.race == "terran"
        assert obs.game.game_time_limit_seconds == 180.0
        assert obs.economy.worker_count >= 8
        assert obs.economy.minerals == obs.resources.minerals
        assert obs.economy.ideal_worker_count >= 16
        assert obs.economy.supply_used == obs.resources.supply_used
        ours = [row for row in obs.zone_state if row.get("known_owner") == "self"]
        assert ours
        resource = next(row for row in obs.map_control.base_resources
                        if row["zone_id"] == ours[0]["zone_id"])
        assert resource["minerals_remaining"] > 0
        assert resource["minerals_initial"] == resource["minerals_remaining"]
        assert resource["vespene_remaining"] > 0
        assert resource["vespene_initial"] == resource["vespene_remaining"]
        assert resource["geyser_slots"] == resource["available_geyser_slots"] == 2
        assert resource["owned_gas_structure_count"] == 0
        assert len(obs.zone_state) == len(obs.zones)
        topology = obs.map_topology
        assert topology["distance_basis"] == "static_terrain_paths"
        assert topology["verified_path_pair_count"] > 0
        assert {row["zone_id"] for row in topology["zones"]} == set(obs.zones)
        main_id = next(row["zone_id"] for row in obs.zone_state if row["zone_role"] == "own_main")
        main_topology = next(row for row in topology["zones"] if row["zone_id"] == main_id)
        assert main_topology["path_distance_from_own_main"] == 0
        assert main_topology["corridor_neighbors"]
        assert any(row["path_distance_from_own_main"] is not None
                   and row["path_distance_from_own_main"] > 0 for row in topology["zones"])
        assert {row["zone_role"] for row in obs.zone_state} >= {
            "own_main", "own_natural", "enemy_main", "enemy_natural"}
        assert next(row for row in obs.zone_state
                    if row["zone_role"] == "own_main")["zone_id"] == ours[0]["zone_id"]
        assert all(row["known_owner"] in {"self", "enemy", "unconfirmed"}
                   and row["vision_state"] in {"visible", "fogged"}
                   and "control" not in row for row in obs.zone_state)
        assert obs.map_control.unconfirmed_expansion_count == sum(
            row["known_owner"] == "unconfirmed" for row in obs.zone_state)
        assert not any(row["visible_enemy_weapon_in_range"] for row in obs.zone_state)
        assert not env.backend.snapshot().info["zones_under_attack"]
        assert obs.map_control.own_base_count == obs.base_count == 1
        assert obs.own_forces.workers.get("scv", 0) == obs.units.get("scv", 0)
        assert sum(row["own_contents"]["units"].get("scv", 0)
                   for row in obs.zone_state) == obs.units.get("scv", 0)
        assert sum(row["own_contents"]["buildings"].get("command_center", 0)
                   for row in obs.zone_state) == obs.buildings.get("command_center", 0)
        assert all(set(row["own_contents"]) == {"units", "buildings"}
                   and set(row["visible_enemy_contents"]) == {"units", "buildings"}
                   and set(row["last_seen_enemy_contents"]) == {"units", "buildings"}
                   for row in obs.zone_state)

        start_time = obs.game_time_seconds
        obs, _, terminated, _ = env.step([
            {"action": "wait", "all_of": [{"condition": "interval"}]}
        ])
        assert not terminated and obs.game_time_seconds - start_time >= 6
        after = next(row for row in obs.map_control.base_resources
                     if row["zone_id"] == resource["zone_id"])
        assert after["minerals_initial"] == resource["minerals_initial"]
        assert after["minerals_remaining"] < resource["minerals_remaining"]
        assert after["vespene_remaining"] == after["vespene_initial"]
        context = env.get_context()
        assert context[0]["content"] == env.get_system_prompt()
        assert "Decision format:" in context[0]["content"]
        assert "Observation guide:" in context[0]["content"]
        assert "\n".join(obs.section_lines()) in context[1]["content"]
        for label in ("Zone State", "Scouting", "Abilities", "Recent Events"):
            assert f"[{label}]" in context[1]["content"]

        env.step([{"action": "build", "target": "supply_depot"}, _wait(6)])
        obs, terminated = _wait_until(
            env, lambda o: o.buildings.get("supply_depot", 0) >= 1, max_steps=16
        )
        assert not terminated
        env.step([{"action": "build", "target": "barracks"}, _wait(6)])
        obs, terminated = _wait_until(
            env, lambda o: o.buildings.get("barracks", 0) >= 1, max_steps=22
        )
        assert obs.buildings.get("barracks", 0) >= 1
        env.step([{"action": "train", "target": "marine", "count": 1}, _wait(6)])
        obs, terminated = _wait_until(
            env, lambda o: o.units.get("marine", 0) >= 1, max_steps=18
        )
        assert obs.own_forces.army.get("marine", 0) >= 1
        assert obs.economy.mineral_income_per_minute >= 0
        assert "orbital_count" not in obs.abilities
        obs, _, terminated, _ = env.step([
            {"action": "train", "target": "banshee", "count": 8},
            {"action": "build", "target": "fusion_core"},
            {"action": "train", "target": "raven", "count": 4},
            {"action": "train", "target": "banshee", "count": 8},
            _wait(1),
        ])
        assert not terminated
        assert [(row["action"], row["target"]) for row in obs.production_priority] == [
            ("train", "banshee"), ("build", "fusion_core"),
            ("train", "raven"), ("train", "banshee")]
        assert all(row["state"] == "waiting_to_start" for row in obs.production_priority)
        assert "[Production Priority]" in env.get_context()[1]["content"]
        assert all(d.demand_id not in env.get_context()[1]["content"]
                   for d in env.task_manager.active_demands())

        env.step([{"action": "build", "target": "refinery"}, _wait(6)])
        obs, terminated = _wait_until(
            env, lambda o: o.buildings.get("refinery", 0) >= 1, max_steps=12
        )
        assert not terminated and obs.buildings.get("refinery", 0) >= 1
        obs, terminated = _wait_until(env, lambda o: next(
            row for row in o.map_control.base_resources
            if row["zone_id"] == resource["zone_id"])["vespene_remaining"] < resource["vespene_initial"],
            max_steps=6)
        assert not terminated
        after = next(row for row in obs.map_control.base_resources
                     if row["zone_id"] == resource["zone_id"])
        assert after["owned_gas_structure_count"] == 1
        assert after["available_geyser_slots"] == 1 and after["geyser_slots"] == 2
        assert 0 < after["vespene_remaining"] < resource["vespene_initial"]
        assert after["vespene_initial"] == resource["vespene_initial"]
        assert after["minerals_initial"] == resource["minerals_initial"]
        assert f' | {after["vespene_remaining"]}/{after["vespene_initial"]} | ' in "\n".join(obs.section_lines())
    finally:
        env.close()


def test_e2e_observed_addon_hosts_and_train_slots():
    """Natural construction only; blocked hosts cannot starve runnable work."""
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig
    env = Environment("sharpy")
    try:
        env.reset(EpisodeConfig(opponent="builtin_easy", game_time_limit_seconds=420))
        env.step([{"action": "build", "target": "supply_depot"},
                  {"action": "build", "target": "refinery"}, _wait(6)])
        obs, ended = _wait_until(env, lambda o: o.buildings.get("supply_depot", 0) >= 1
                                 and o.buildings.get("refinery", 0) >= 1, max_steps=20)
        assert not ended
        env.step([{"action": "build", "target": "barracks"}, _wait(6)])
        obs, ended = _wait_until(env, lambda o: o.buildings.get("barracks", 0) >= 1)
        assert not ended
        obs, _, ended, _ = env.step([
            {"action": "train", "target": "marine", "count": 2},
            {"action": "build", "target": "barracks_techlab"}, _wait(1)])
        assert not ended
        assert obs.building["barracks_techlab"]["waiting_for"] == "addon_host_busy"
        assert obs.training["marine"]["waiting_for"] == "producer_busy"
        obs, ended = _wait_until(env, lambda o: o.buildings.get("barracks_techlab", 0) >= 1,
                                 max_steps=30)
        assert not ended and obs.buildings["barracks_techlab"] == 1
        obs, _, ended, _ = env.step([
            {"action": "build", "target": "barracks_techlab"},
            {"action": "train", "target": "marine", "count": 1}, _wait(1)])
        assert not ended
        assert obs.building["barracks_techlab"]["waiting_for"] == "addon_host_unavailable"
        assert "addon_host_unavailable" in env.get_context()[1]["content"]
        obs, ended = _wait_until(env, lambda o: o.units.get("marine", 0) >= 3, max_steps=8)
        assert not ended and obs.units["marine"] == 3
        assert obs.building["barracks_techlab"]["waiting_to_start"] == 1
    finally:
        env.close()


def test_e2e_fixed_decision_safety_cap_returns_without_ending_episode():
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(opponent="builtin_veryeasy", game_time_limit_seconds=180))
        for _ in range(2):
            started_at = obs.game.game_time_seconds
            obs, _, terminated, _ = env.step([{
                "action": "wait", "any_of": [{"condition": "interval", "seconds": 300}],
                "all_of": [{"condition": "unit_count_at_least", "unit": "battlecruiser", "count": 999}],
            }])
            elapsed = obs.game.game_time_seconds - started_at
            assert 60 <= elapsed <= 61, "cap must return within one simulation sampling step"
            assert not terminated and not obs.terminated
            assert_obs_consistent(obs)
    finally:
        env.close()


def test_e2e_zone_threat_wait_and_clear(monkeypatch) -> None:
    """Controlled enemy placement only; no own micro or debug economy changes."""
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths

    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId
    from sharpy.plans import BuildOrder
    from sharpy.plans.acts import ActBase
    from sc2bench_env import Environment
    from sc2bench_env.backends.sharpy.races import terran
    from sc2bench_env.interface.config import EpisodeConfig

    state = {"spawn_at": None, "spawned": False, "clear": False, "visible": False}

    class ThreatSetup(ActBase):
        async def execute(self):
            if (state["spawn_at"] is not None and self.ai.time >= state["spawn_at"]
                    and not state["spawned"]):
                worker = self.ai.workers.closest_to(self.ai.start_location)
                await self.ai.client.debug_create_unit([
                    [UnitTypeId.ROACH, 1, worker.position.towards(self.ai.start_location, 1), 2]
                ])
                state["spawned"] = True
            enemies = self.ai.enemy_units(UnitTypeId.ROACH)
            state["visible"] |= any(enemy.is_visible for enemy in enemies)
            if state["clear"] and enemies:
                await self.ai.client.debug_kill_unit(enemies)
            return True

    original = terran.TerranAdapter.create_tactics
    monkeypatch.setattr(terran.TerranAdapter, "create_tactics",
                        lambda adapter: BuildOrder([ThreatSetup(), original(adapter)]))
    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(decision_interval_seconds=2,
                                     game_time_limit_seconds=90,
                                     opponent="builtin_veryeasy"))
        assert not any(row["visible_enemy_weapon_in_range"] for row in obs.zone_state)
        home = next(row["zone_id"] for row in obs.zone_state if row["known_owner"] == "self")
        neutral = next(row["zone_id"] for row in obs.zone_state
                       if row["known_owner"] == "unconfirmed")
        start = obs.game_time_seconds
        obs, _, terminated, _ = env.step([{"action": "wait", "any_of": [
            {"condition": "zone_under_attack", "zone": neutral},
            {"condition": "interval", "seconds": 3}]}])
        assert not terminated and obs.game_time_seconds - start >= 3
        state["spawn_at"] = obs.game_time_seconds + 1
        start = obs.game_time_seconds
        obs, _, terminated, _ = env.step([{"action": "wait", "any_of": [
            {"condition": "zone_under_attack", "zone": home},
            {"condition": "interval", "seconds": 10}]}])
        assert not terminated and state["spawned"] and state["visible"]
        assert 0 < obs.game_time_seconds - start < 10
        assert next(row for row in obs.zone_state if row["zone_id"] == home)["visible_enemy_weapon_in_range"]
        assert sum(row["visible_enemy_contents"]["units"].get("roach", 0)
                   for row in obs.zone_state) >= 1
        assert home in env.backend.snapshot().info["zones_under_attack"]
        assert_obs_consistent(obs)
        state["clear"] = True
        obs, _, terminated, _ = env.step([_wait(2)])
        assert not terminated and not any(row["visible_enemy_weapon_in_range"] for row in obs.zone_state)
        assert not any(row["visible_enemy_contents"]["units"].get("roach", 0)
                       for row in obs.zone_state)
        assert not any(row["last_seen_enemy_contents"]["units"].get("roach", 0)
                       for row in obs.zone_state)
        assert not env.backend.snapshot().info["zones_under_attack"]
        assert_obs_consistent(obs)
    finally:
        env.close()


def test_e2e_zone_enemy_history_and_reobserve_clear(monkeypatch) -> None:
    """A real scout creates history; revisiting an empty last position clears it."""
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths

    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId
    from sharpy.plans import BuildOrder
    from sharpy.plans.acts import ActBase
    from sc2bench_env import Environment
    from sc2bench_env.backends.sharpy.races import terran
    from sc2bench_env.interface.config import EpisodeConfig

    state = {"target": None, "spawned": False, "kill": False, "killed": False}

    class HistorySetup(ActBase):
        async def execute(self):
            registry = getattr(self.ai, "zone_registry", None)
            zone = (registry.resolve_zone(self.ai.zone_manager, state["target"])
                    if registry is not None and state["target"] else None)
            if zone is not None and not state["spawned"]:
                await self.ai.client.debug_create_unit([
                    [UnitTypeId.PYLON, 1, zone.center_location, 2]
                ])
                state["spawned"] = True
            # The controlled fixture may remove an opponent while it is in fog.
            # Read the episode tracker's internal tag only for debug setup; the
            # public Observation never exposes it and the second platform scout
            # must still invalidate the last-known position.
            tracker = getattr(registry, "enemies", None)
            tags = [record.tag for record in getattr(tracker, "_records", {}).values()
                    if record.name == "pylon"]
            if state["kill"] and tags and not state["killed"]:
                await self.ai.client.debug_kill_unit(tags)
                state["killed"] = True
            return True

    original = terran.TerranAdapter.create_tactics
    monkeypatch.setattr(terran.TerranAdapter, "create_tactics",
                        lambda adapter: BuildOrder([HistorySetup(), original(adapter)]))
    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(decision_interval_seconds=2,
                                     game_time_limit_seconds=150,
                                     opponent="builtin_veryeasy"))
        home = next(row["zone_id"] for row in obs.zone_state
                    if row["zone_role"] == "own_main")
        target = next(row["zone_id"] for row in obs.zone_state
                      if row["zone_role"] == "own_natural")
        state["target"] = target

        obs, feedback, terminated, _ = env.step([
            {"action": "scout", "route": [target, home]}, _wait(0.25)])
        assert not terminated and feedback.receipts[0].result == "accepted"
        saw_current = False
        saw_history = False
        for _ in range(120):
            row = next(item for item in obs.zone_state if item["zone_id"] == target)
            saw_current |= row["visible_enemy_contents"]["buildings"].get("pylon", 0) == 1
            if (row["last_seen_enemy_contents"]["buildings"].get("pylon", 0) == 1
                    and row["enemy_information_age_seconds"] > 0):
                saw_history = True
                break
            obs, _, terminated, _ = env.step([_wait(0.25)])
            assert not terminated
        # The worker can cross the visible Pylon between two externally
        # returned snapshots; the tracker can only create this history from a
        # genuine current sighting. Persistent current output is covered by the
        # controlled Roach test above.
        assert state["spawned"] and saw_history

        state["kill"] = True
        obs, feedback, terminated, _ = env.step([
            {"action": "scout", "route": [target, home]}, _wait(0.5)])
        assert not terminated and feedback.receipts[0].result == "accepted"
        cleared = False
        for _ in range(80):
            row = next(item for item in obs.zone_state if item["zone_id"] == target)
            if (state["killed"]
                    and not row["visible_enemy_contents"]["buildings"].get("pylon", 0)
                    and not row["last_seen_enemy_contents"]["buildings"].get("pylon", 0)):
                cleared = True
                break
            obs, _, terminated, _ = env.step([_wait(0.5)])
            assert not terminated
        assert cleared
        assert_obs_consistent(obs)
    finally:
        env.close()
