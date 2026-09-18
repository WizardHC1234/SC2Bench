"""Environment + FakeBackend tests for corrected Phase 0 contract."""

from __future__ import annotations

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.config import EpisodeConfig


def _cfg(**kwargs) -> EpisodeConfig:
    defaults = dict(decision_interval_seconds=15.0, game_time_limit_seconds=400.0)
    defaults.update(kwargs)
    return EpisodeConfig(**defaults)


def _wait(seconds: float = 15.0) -> dict:
    return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}


def test_system_prompt_available() -> None:
    env = Environment(FakeBackend())
    assert "wait" in env.get_system_prompt()
    env.close()


def test_duplicate_build_appends_two_demands() -> None:
    backend = FakeBackend(mineral_income_per_second=40.0)
    env = Environment(backend)
    env.reset(_cfg(decision_interval_seconds=5.0))
    env.step([{"action": "build", "target": "supply_depot"}, _wait(20)])
    for _ in range(8):
        obs, _, _, _ = env.step([_wait(10)])
        if obs.buildings.get("supply_depot", 0) >= 1 or obs.building.get("supply_depot", {}).get("under_construction", 0):
            break

    obs, feedback, _, _ = env.step(
        [
            {"action": "build", "target": "barracks"},
            {"action": "build", "target": "barracks"},
            _wait(5),
        ]
    )
    assert [r.result for r in feedback.receipts] == ["accepted", "accepted"]
    barracks = obs.building.get("barracks", {})
    assert barracks.get("waiting_to_start", 0) + barracks.get("worker_en_route", 0) + barracks.get(
        "under_construction", 0
    ) >= 2
    env.close()


def test_build_action_ends_when_entity_appears() -> None:
    backend = FakeBackend(mineral_income_per_second=30.0)
    env = Environment(backend)
    env.reset(_cfg(decision_interval_seconds=5.0))
    env.step([{"action": "build", "target": "supply_depot"}, _wait(5)])
    saw_under = False
    for _ in range(20):
        obs, _, _, _ = env.step([_wait(5)])
        depot = obs.building.get("supply_depot", {})
        if depot.get("under_construction", 0) >= 1:
            saw_under = True
        # Once the action finished, waiting/en_route should clear even if still constructing.
        active_build = [
            d
            for d in env.task_manager.active_demands()
            if d.action == "build" and d.target == "supply_depot"
        ]
        if saw_under and not active_build:
            break
    assert saw_under
    assert not any(
        d.action == "build" and d.target == "supply_depot" and d.is_active
        for d in env.task_manager.demands.values()
    )
    # World construction may still finish afterward.
    for _ in range(20):
        obs, _, _, _ = env.step([_wait(5)])
        if obs.buildings.get("supply_depot", 0) >= 1:
            break
    assert obs.buildings.get("supply_depot", 0) >= 1
    env.close()


def test_train_append_and_living_split() -> None:
    backend = FakeBackend(mineral_income_per_second=40.0)
    env = Environment(backend)
    env.reset(_cfg(decision_interval_seconds=10.0, game_time_limit_seconds=800.0))
    env.step([{"action": "build", "target": "supply_depot"}, _wait(20)])
    for _ in range(10):
        obs, _, _, _ = env.step([_wait(10)])
        if obs.buildings.get("supply_depot", 0) >= 1:
            break
    env.step([{"action": "build", "target": "supply_depot"}, _wait(20)])
    for _ in range(10):
        obs, _, _, _ = env.step([_wait(10)])
        if obs.buildings.get("supply_depot", 0) >= 2:
            break
    env.step([{"action": "build", "target": "barracks"}, _wait(20)])
    for _ in range(15):
        obs, _, _, _ = env.step([_wait(10)])
        if obs.buildings.get("barracks", 0) >= 1:
            break

    env.step(
        [
            {"action": "train", "target": "marine", "count": 8},
            {"action": "train", "target": "marine", "count": 4},
            _wait(10),
        ]
    )
    for _ in range(80):
        obs, _, terminated, _ = env.step([_wait(10)])
        if obs.units.get("marine", 0) >= 12:
            break
        if terminated:
            break
    assert obs.units.get("marine", 0) == 12
    assert obs.own_forces.army.get("marine", 0) == 12
    assert "marine" not in obs.training  # completed stock is not an active order
    env.close()


def test_cancel_waiting_build_only() -> None:
    backend = FakeBackend(mineral_income_per_second=5.0)
    env = Environment(backend)
    # Keep minerals low so barracks stays waiting_to_start behind depot demand.
    env.reset(_cfg(decision_interval_seconds=5.0))
    env.step(
        [
            {"action": "build", "target": "supply_depot"},
            {"action": "build", "target": "barracks"},
            _wait(5),
        ]
    )
    obs, _, _, _ = env.step([_wait(5)])
    assert obs.building.get("barracks", {}).get("waiting_to_start", 0) >= 1
    env.step(
        [
            {"action": "cancel", "target_action": "build", "target": "barracks"},
            _wait(5),
        ]
    )
    obs, _, _, _ = env.step([_wait(5)])
    assert obs.building.get("barracks", {}).get("waiting_to_start", 0) == 0
    env.close()


def test_invalid_decision_does_not_advance_as_success() -> None:
    env = Environment(FakeBackend())
    env.reset(_cfg())
    obs, feedback, terminated, info = env.step([{"action": "build", "target": "barracks"}])
    assert not terminated
    assert "error" in info
    assert feedback.events
    assert feedback.events[0]["type"] == "decision_rejected"
    env.close()


def test_observation_has_no_task_id_field() -> None:
    env = Environment(FakeBackend(mineral_income_per_second=30.0))
    obs = env.reset(_cfg())
    payload = obs.to_dict()
    assert "tasks" not in payload
    obs, feedback, _, _ = env.step([{"action": "build", "target": "supply_depot"}, _wait(10)])
    assert feedback.receipts
    assert "task_id" not in feedback.receipts[0].to_dict()
    assert "building" in obs.to_dict()
    env.close()
