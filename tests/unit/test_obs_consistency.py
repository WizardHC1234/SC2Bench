"""Phase 0 FakeBackend observation section checks."""

from __future__ import annotations

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.config import EpisodeConfig

from tests.helpers.obs_invariants import assert_obs_consistent


def _wait(seconds: float = 15.0) -> dict:
    return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}


def test_building_and_training_sections() -> None:
    backend = FakeBackend(mineral_income_per_second=40.0)
    env = Environment(backend)
    obs = env.reset(EpisodeConfig(decision_interval_seconds=10.0, game_time_limit_seconds=400.0))
    assert_obs_consistent(obs)
    assert obs.building["command_center"]["completed"] == 1

    env.step([{"action": "build", "target": "supply_depot"}, _wait(20)])
    for _ in range(12):
        obs, _, _, _ = env.step([_wait(10)])
        assert_obs_consistent(obs)
        if obs.buildings.get("supply_depot", 0) >= 1:
            break
    assert obs.building["supply_depot"]["completed"] >= 1

    env.step([{"action": "build", "target": "barracks"}, _wait(20)])
    for _ in range(15):
        obs, _, _, _ = env.step([_wait(10)])
        if obs.buildings.get("barracks", 0) >= 1:
            break
    env.step([{"action": "train", "target": "marine", "count": 3}, _wait(10)])
    for _ in range(20):
        obs, _, _, _ = env.step([_wait(10)])
        assert_obs_consistent(obs)
        if obs.units.get("marine", 0) >= 3:
            break
    assert obs.own_forces.army["marine"] >= 3
    env.close()


def test_reset_clears_sections() -> None:
    env = Environment(FakeBackend(mineral_income_per_second=30.0))
    env.reset(EpisodeConfig(decision_interval_seconds=10.0))
    env.step([{"action": "build", "target": "supply_depot"}, _wait(20)])
    for _ in range(10):
        obs, _, _, _ = env.step([_wait(10)])
        if obs.buildings.get("supply_depot", 0) >= 1:
            break
    obs = env.reset(EpisodeConfig(decision_interval_seconds=10.0))
    assert_obs_consistent(obs)
    assert obs.buildings == {"command_center": 1}
    assert obs.training == {}
    env.close()
