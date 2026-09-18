"""Waiting / failure path checks for FakeBackend."""

from __future__ import annotations

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.config import EpisodeConfig


def _wait(seconds: float = 10.0) -> dict:
    return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}


def test_barracks_waits_for_depot_prerequisite() -> None:
    backend = FakeBackend(mineral_income_per_second=40.0)
    env = Environment(backend)
    env.reset(EpisodeConfig(decision_interval_seconds=5.0, game_time_limit_seconds=200.0))
    env.step([{"action": "build", "target": "barracks"}, _wait(5)])
    obs, _, _, _ = env.step([_wait(5)])
    barracks = obs.building.get("barracks", {})
    assert barracks.get("waiting_to_start", 0) >= 1
    assert str(barracks.get("waiting_for", "")).startswith("prerequisite")
    env.close()


def test_unknown_research_rejected_by_catalog() -> None:
    backend = FakeBackend(mineral_income_per_second=50.0, vespene_income_per_second=50.0)
    env = Environment(backend)
    obs = env.reset(EpisodeConfig(decision_interval_seconds=5.0))
    obs, feedback, terminated, info = env.step(
        [{"action": "research", "target": "not_a_real_upgrade"}, _wait(5)]
    )
    assert not terminated
    assert feedback.events
    assert feedback.events[0]["type"] == "decision_rejected"
    assert "unsupported research target" in feedback.events[0]["reason"]
    assert "error" in info
    assert not env.task_manager.active_demands()
    env.close()


def test_research_action_ends_when_queued() -> None:
    backend = FakeBackend(mineral_income_per_second=40.0, vespene_income_per_second=40.0)
    env = Environment(backend)
    env.reset(EpisodeConfig(decision_interval_seconds=10.0, game_time_limit_seconds=500.0))

    env.step([{"action": "build", "target": "supply_depot"}, _wait(15)])
    for _ in range(12):
        obs, _, _, _ = env.step([_wait(10)])
        if obs.buildings.get("supply_depot", 0) >= 1:
            break
    env.step([{"action": "build", "target": "barracks"}, _wait(20)])
    for _ in range(20):
        obs, _, _, _ = env.step([_wait(10)])
        if obs.buildings.get("barracks", 0) >= 1:
            break
    env.step([{"action": "build", "target": "barracks_techlab"}, _wait(20)])
    for _ in range(20):
        obs, _, _, _ = env.step([_wait(10)])
        if obs.buildings.get("barracks_techlab", 0) >= 1:
            break

    env.step([{"action": "research", "target": "stimpack"}, _wait(5)])
    for _ in range(10):
        obs, _, _, _ = env.step([_wait(5)])
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
    assert not any(
        d.action == "research" and d.target == "stimpack" and d.is_active
        for d in env.task_manager.demands.values()
    )
    assert obs.research.get("stimpack") in {"in_progress", "completed"} or "stimpack" in obs.upgrades

    # Idempotent while researching / researched.
    obs, feedback, _, _ = env.step([{"action": "research", "target": "stimpack"}, _wait(5)])
    assert feedback.receipts[0].result == "idempotent_noop"
    env.close()
