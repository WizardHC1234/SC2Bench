"""Core Observation sections: game, economy, map_control, zone_state, own_forces."""

from __future__ import annotations

import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FAKE_ZONE_COUNT, FakeBackend
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.observations import OBSERVATION_SECTIONS, render_observation_text

from tests.helpers.obs_invariants import assert_obs_consistent


def _wait(seconds: float = 1.0) -> dict:
    return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}


def test_reset_exposes_core_observation_sections() -> None:
    env = Environment(FakeBackend())
    obs = env.reset(
        EpisodeConfig(decision_interval_seconds=5.0, game_time_limit_seconds=600.0)
    )
    assert_obs_consistent(obs)
    assert obs.game.race == "terran"
    assert obs.game.game_time_limit_seconds == 600.0
    assert obs.game.seconds_remaining == 600.0
    assert obs.economy.worker_count == 12
    assert obs.economy.ideal_worker_count >= 16
    assert obs.economy.supply_left == 3
    assert obs.economy.mineral_income_per_minute > 0
    assert obs.map_control.own_base_count == 1
    assert obs.map_control.known_enemy_base_count == 1
    assert obs.map_control.unconfirmed_expansion_count == FAKE_ZONE_COUNT - 2
    assert len(obs.zone_state) == FAKE_ZONE_COUNT
    assert obs.zone_state[0]["known_owner"] == "self"
    assert obs.zone_state[0]["zone_role"] == "own_main"
    assert obs.zone_state[0]["vision_state"] == "visible"
    assert obs.zone_state[0]["own_contents"] == {
        "units": {"scv": 12}, "buildings": {"command_center": 1}}
    assert obs.zone_state[0]["visible_enemy_contents"] == {"units": {}, "buildings": {}}
    assert obs.zone_state[0]["last_seen_enemy_contents"] == {"units": {}, "buildings": {}}
    assert obs.zone_state[0]["enemy_information_age_seconds"] is None
    assert obs.zone_state[-1]["known_owner"] == "enemy"
    assert obs.zone_state[-1]["zone_role"] == "enemy_main"
    assert obs.zone_state[-1]["vision_state"] == "fogged"
    assert all("control" not in row for row in obs.zone_state)
    map_payload = obs.map_control.to_dict()
    assert "active_mining_base_count" not in map_payload
    assert "neutral_expansion_count" not in map_payload
    resource = obs.map_control.base_resources[0]
    assert resource["zone_id"] == obs.zone_state[0]["zone_id"]
    assert resource["minerals_remaining"] == resource["minerals_initial"] == 10500
    assert resource["vespene_remaining"] == resource["vespene_initial"] == 4500
    assert "zone_0 | 10500/10500 | 4500/4500" in "\n".join(obs.section_lines())
    assert "minerals_remaining" not in obs.zone_state[0]
    assert obs.own_forces.workers["scv"] == 12
    assert obs.own_forces.army == {}
    assert obs.abilities["orbital_energies"] == []
    assert "orbital_count" not in obs.abilities
    payload = obs.to_dict()
    assert list(payload) == [key for key, _ in OBSERVATION_SECTIONS]
    assert "game" in payload and "economy" in payload and "zone_state" in payload
    assert not ({"game_time_seconds", "race", "enemy_race", "resources", "units",
                 "buildings", "base_count", "zones", "upgrades", "orbital_count",
                 "scan_ready", "mule_ready", "info"} & set(payload))
    assert payload["game"]["game_time_seconds"] == obs.game_time_seconds
    assert payload["economy"]["minerals"] == obs.resources.minerals
    assert [row["zone_id"] for row in payload["zone_state"]] == obs.zones
    assert payload["building"]["command_center"]["completed"] == obs.base_count
    assert payload["own_forces"]["workers"]["scv"] == obs.units["scv"]
    assert "total" not in payload["own_forces"]
    assert "orbital_count" not in payload["abilities"]
    text = "\n".join(obs.section_lines())
    assert "[Zones]" not in text and "[Upgrades]" not in text
    env.close()


def test_random_enemy_race_does_not_expose_configuration_as_observed_race() -> None:
    env = Environment(FakeBackend())
    try:
        obs = env.reset(EpisodeConfig(enemy_race="random"))
        assert obs.game.enemy_race == "unknown"
        assert obs.to_dict()["game"]["enemy_race"] == "unknown"
    finally:
        env.close()


def test_own_forces_army_stays_separate_from_training() -> None:
    backend = FakeBackend(mineral_income_per_second=40.0)
    env = Environment(backend)
    env.reset(EpisodeConfig(decision_interval_seconds=5.0, game_time_limit_seconds=200.0))
    backend.buildings["supply_depot"] = 1
    backend.buildings["barracks"] = 1
    backend.supply_cap = 23
    backend.minerals = 200
    obs, _, _, _ = env.step([{"action": "train", "target": "marine", "count": 2}, _wait(20)])
    assert_obs_consistent(obs)
    living = int(obs.units.get("marine", 0))
    assert living >= 1
    assert obs.own_forces.army.get("marine", 0) == living
    assert "living" not in obs.training.get("marine", {})
    env.close()


def test_fake_map_counts_follow_confirmed_owner_rows_after_base_loss() -> None:
    backend = FakeBackend()
    env = Environment(backend)
    env.reset()
    backend.buildings["command_center"] = 0
    obs, _, _, _ = env.step([_wait()])
    assert obs.map_control.own_base_count == 0
    assert obs.map_control.known_enemy_base_count == 1
    assert obs.map_control.unconfirmed_expansion_count == FAKE_ZONE_COUNT - 1
    assert sum(row["known_owner"] == "self" for row in obs.zone_state) == 0
    assert sum(row["known_owner"] == "enemy" for row in obs.zone_state) == 1
    env.close()


@pytest.mark.parametrize("remaining,initial,ratio", [(0, 900, "0/900"),
                                                     (None, 900, "?/900"),
                                                     (500, None, "500/?")])
def test_resource_ratio_text_preserves_numeric_api_and_unknowns(remaining, initial, ratio):
    row = {"zone_id": "zone_0", "minerals_remaining": remaining,
           "minerals_initial": initial, "vespene_remaining": None, "vespene_initial": None}
    original = dict(row)
    text = render_observation_text({"map_control": {"base_resources": [row]}})
    assert f"zone_0 | {ratio} | ?/?" in text
    assert row == original
