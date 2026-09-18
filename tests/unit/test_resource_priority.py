"""Cross-type resource priority (PLATFORM_PLAN §5.3) on FakeBackend."""

from __future__ import annotations

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.config import EpisodeConfig


def _wait(seconds: float = 1.0) -> dict:
    return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}


def test_earlier_resource_wait_blocks_later_cheaper_train() -> None:
    backend = FakeBackend(mineral_income_per_second=0.0, vespene_income_per_second=0.0)
    env = Environment(backend)
    env.reset(EpisodeConfig(decision_interval_seconds=1.0, game_time_limit_seconds=60.0))
    backend.buildings["supply_depot"] = 1
    backend.buildings["barracks"] = 1
    backend.supply_cap = 22
    backend.minerals = 100  # engineering_bay=125, marine=50
    backend.vespene = 0

    obs, _, _, _ = env.step(
        [
            {"action": "build", "target": "engineering_bay"},
            {"action": "train", "target": "marine", "count": 1},
            _wait(1),
        ]
    )
    bay = obs.building.get("engineering_bay", {})
    marine = obs.training.get("marine", {})
    assert bay.get("waiting_to_start", 0) >= 1
    assert marine.get("waiting_to_produce", 0) >= 1
    assert marine.get("in_production", 0) == 0
    assert backend.minerals == 100
    env.close()


def test_leftover_after_earlier_spend_can_fund_later() -> None:
    backend = FakeBackend(mineral_income_per_second=0.0, vespene_income_per_second=0.0)
    env = Environment(backend)
    env.reset(EpisodeConfig(decision_interval_seconds=1.0, game_time_limit_seconds=60.0))
    backend.buildings["supply_depot"] = 1
    backend.buildings["barracks"] = 1
    backend.supply_cap = 22
    backend.minerals = 175  # engineering_bay 125 + marine 50
    backend.vespene = 0

    obs, _, _, _ = env.step(
        [
            {"action": "build", "target": "engineering_bay"},
            {"action": "train", "target": "marine", "count": 1},
            _wait(1),
        ]
    )
    bay = obs.building.get("engineering_bay", {})
    marine = obs.training.get("marine", {})
    assert bay.get("worker_en_route", 0) + bay.get("under_construction", 0) >= 1
    assert marine.get("in_production", 0) >= 1
    assert backend.minerals == 0
    env.close()


def test_prereq_blocked_earlier_does_not_reserve() -> None:
    backend = FakeBackend(mineral_income_per_second=0.0, vespene_income_per_second=0.0)
    env = Environment(backend)
    env.reset(EpisodeConfig(decision_interval_seconds=1.0, game_time_limit_seconds=60.0))
    backend.buildings["supply_depot"] = 1
    backend.buildings.pop("barracks", None)
    backend.supply_cap = 22
    backend.minerals = 150
    backend.vespene = 0

    obs, _, _, _ = env.step(
        [
            {"action": "train", "target": "marine", "count": 1},
            {"action": "build", "target": "barracks"},
            _wait(1),
        ]
    )
    marine = obs.training.get("marine", {})
    barracks = obs.building.get("barracks", {})
    assert marine.get("waiting_to_produce", 0) >= 1
    assert marine.get("in_production", 0) == 0
    marine_demands = [
        d for d in env.task_manager.active_demands() if d.action == "train" and d.target == "marine"
    ]
    assert marine_demands
    assert str(marine_demands[0].waiting_for or "").startswith("prerequisite")
    assert barracks.get("worker_en_route", 0) + barracks.get("under_construction", 0) >= 1
    assert backend.minerals == 0
    env.close()
