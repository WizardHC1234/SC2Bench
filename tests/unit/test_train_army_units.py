"""FakeBackend coverage for first-wave army unit production chains."""

from __future__ import annotations

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.config import EpisodeConfig

from tests.helpers.obs_invariants import assert_obs_consistent


def _wait(seconds: float = 5.0) -> dict:
    return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}


def _boot(env: Environment, backend: FakeBackend) -> None:
    backend.buildings["supply_depot"] = 2
    backend.buildings["barracks"] = 1
    backend.buildings["barracks_techlab"] = 1
    backend.buildings["factory"] = 1
    backend.buildings["factory_techlab"] = 1
    backend.buildings["starport"] = 1
    backend.buildings["starport_techlab"] = 1
    backend.buildings["refinery"] = 2
    backend.supply_cap = 50
    backend.minerals = 800
    backend.vespene = 400


def test_fake_trains_first_wave_army_units() -> None:
    backend = FakeBackend(mineral_income_per_second=0.0, vespene_income_per_second=0.0)
    env = Environment(backend)
    env.reset(EpisodeConfig(decision_interval_seconds=5.0, game_time_limit_seconds=180.0))
    _boot(env, backend)

    obs, feedback, _, _ = env.step(
        [
            {"action": "train", "target": "marauder", "count": 1},
            {"action": "train", "target": "siege_tank", "count": 1},
            {"action": "train", "target": "medivac", "count": 1},
            {"action": "train", "target": "banshee", "count": 1},
            _wait(5),
        ]
    )
    assert [r.result for r in feedback.receipts] == ["accepted"] * 4
    # One Starport serializes Medivac then Banshee; they do not own separate
    # producer slots simply because their target names differ.
    assert obs.training["medivac"]["in_production"] == 1
    assert obs.training["banshee"]["in_production"] == 0
    for _ in range(18):
        obs, _, _, _ = env.step([_wait(5)])
        assert_obs_consistent(obs)
        if (
            obs.units.get("marauder", 0) >= 1
            and obs.units.get("siege_tank", 0) >= 1
            and obs.units.get("medivac", 0) >= 1
            and obs.units.get("banshee", 0) >= 1
        ):
            break
    assert obs.units.get("marauder", 0) >= 1
    assert obs.units.get("siege_tank", 0) >= 1
    assert obs.units.get("medivac", 0) >= 1
    assert obs.units.get("banshee", 0) >= 1
    assert obs.own_forces.army.get("marauder", 0) >= 1
    assert obs.own_forces.army.get("siege_tank", 0) >= 1
    env.close()


def test_fake_marauder_waits_for_techlab() -> None:
    backend = FakeBackend(mineral_income_per_second=0.0, vespene_income_per_second=0.0)
    env = Environment(backend)
    env.reset(EpisodeConfig(decision_interval_seconds=2.0, game_time_limit_seconds=60.0))
    backend.buildings["supply_depot"] = 1
    backend.buildings["barracks"] = 1
    backend.supply_cap = 30
    backend.minerals = 200
    backend.vespene = 50

    obs, _, _, _ = env.step(
        [{"action": "train", "target": "marauder", "count": 1}, _wait(2)]
    )
    assert obs.training.get("marauder", {}).get("waiting_to_produce", 0) >= 1
    assert obs.units.get("marauder", 0) == 0

    backend.buildings["barracks_techlab"] = 1
    obs, _, _, _ = env.step([_wait(25)])
    assert obs.units.get("marauder", 0) >= 1
    env.close()
