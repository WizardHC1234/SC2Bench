"""Structured wait condition parsing and FakeBackend wake behavior."""

from __future__ import annotations

import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.actions import ActionValidationError, parse_decision
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.runtime.scheduler import trigger_from_wait, trigger_satisfied


def test_reject_action_id_in_model_json() -> None:
    with pytest.raises(ActionValidationError, match="harness-only"):
        parse_decision(
            [
                {"action": "build", "target": "barracks", "action_id": "a1"},
                {"action": "wait"},
            ]
        )


def test_reject_build_orbital_command() -> None:
    with pytest.raises(ActionValidationError, match="upgrade"):
        parse_decision(
            [
                {"action": "build", "target": "orbital_command"},
                {"action": "wait"},
            ]
        )


def test_env_retry_ids_dedup() -> None:
    from sc2bench_env import Environment
    from sc2bench_env.backends.fake import FakeBackend
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment(FakeBackend(mineral_income_per_second=40.0))
    env.reset(EpisodeConfig(decision_interval_seconds=5.0))
    decision = [
        {"action": "build", "target": "supply_depot"},
        {"action": "wait", "any_of": [{"condition": "interval", "seconds": 5}]},
    ]
    _, feedback1, _, _ = env.step(decision, retry_ids=["retry-1"])
    _, feedback2, _, _ = env.step(decision, retry_ids=["retry-1"])
    assert feedback1.receipts[0].result == "accepted"
    assert feedback2.receipts[0].result == "ignored_duplicate_action_id"
    env.close()


def test_parse_extended_wait_conditions() -> None:
    batch = parse_decision(
        [
            {
                "action": "wait",
                "any_of": [
                    {"condition": "supply_left_at_most", "amount": 2},
                    {"condition": "unit_count_at_least", "unit": "marine", "count": 8},
                    {"condition": "scan_ready"},
                ],
                "all_of": [
                    {"condition": "building_count_at_least", "building": "barracks", "count": 1},
                ],
            }
        ]
    )
    assert {item.condition for item in batch.wait.any_of} == {
        "supply_left_at_most",
        "unit_count_at_least",
        "scan_ready",
    }
    assert batch.wait.all_of[0].condition == "building_count_at_least"


def test_wait_resource_wakes_before_interval() -> None:
    backend = FakeBackend(mineral_income_per_second=50.0)
    env = Environment(backend)
    env.reset(EpisodeConfig(decision_interval_seconds=30.0, game_time_limit_seconds=120.0))
    start = env.backend.snapshot().game_time_seconds
    obs, _, _, _ = env.step(
        [
            {
                "action": "wait",
                "any_of": [
                    {"condition": "resource_at_least", "resource": "minerals", "amount": 200},
                    {"condition": "interval", "seconds": 60},
                ],
            }
        ]
    )
    elapsed = obs.game_time_seconds - start
    assert obs.resources.minerals >= 200
    assert elapsed < 60
    env.close()


def test_wait_unit_count_predicate() -> None:
    backend = FakeBackend()
    backend.units["marine"] = 3
    snap = backend.snapshot()
    trigger = trigger_from_wait(
        parse_decision(
            [
                {
                    "action": "wait",
                    "any_of": [
                        {"condition": "unit_count_at_least", "unit": "marine", "count": 3},
                    ],
                }
            ]
        ).wait,
        default_interval_seconds=5.0,
        max_game_time_seconds=60.0,
    )
    assert trigger_satisfied(trigger, snapshot=snap, wait_started_at=0.0)


@pytest.mark.parametrize("group", ["any_of", "all_of"])
@pytest.mark.parametrize("seconds", [None, 7.0])
def test_interval_defaults_match_in_either_group(group, seconds):
    condition = {"condition": "interval"}
    if seconds is not None:
        condition["seconds"] = seconds
    trigger = trigger_from_wait(
        parse_decision([{"action": "wait", group: [condition]}]).wait,
        default_interval_seconds=5.0, max_game_time_seconds=60.0,
    )
    expected = 5.0 if seconds is None else seconds
    backend = FakeBackend()
    backend.game_time_seconds = 10 + expected - .1
    assert not trigger_satisfied(trigger, snapshot=backend.snapshot(), wait_started_at=10)
    backend.game_time_seconds = 10 + expected
    assert trigger_satisfied(trigger, snapshot=backend.snapshot(), wait_started_at=10)


def test_all_of_default_interval_advances_the_environment():
    env = Environment(FakeBackend())
    try:
        initial = env.reset(EpisodeConfig(decision_interval_seconds=7, game_time_limit_seconds=30))
        obs, _, terminated, _ = env.step([{"action": "wait", "all_of": [{"condition": "interval"}]}])
        assert not terminated
        assert obs.game_time_seconds - initial.game_time_seconds >= 7
    finally:
        env.close()


def test_any_of_interval_does_not_bypass_all_of_gate():
    backend = FakeBackend()
    trigger = trigger_from_wait(parse_decision([{
        "action": "wait",
        "any_of": [{"condition": "interval", "seconds": 2}],
        "all_of": [{"condition": "resource_at_least", "resource": "minerals", "amount": 400}],
    }]).wait, default_interval_seconds=5, max_game_time_seconds=60)
    backend.game_time_seconds = 10
    backend.minerals = 399
    assert not trigger_satisfied(trigger, snapshot=backend.snapshot(), wait_started_at=0)
    backend.minerals = 400
    assert trigger_satisfied(trigger, snapshot=backend.snapshot(), wait_started_at=0)


@pytest.mark.parametrize("wait", [
    {"action": "wait", "any_of": [
        {"condition": "unit_count_at_least", "unit": "marine", "count": 999}]},
    {"action": "wait", "all_of": [
        {"condition": "resource_at_least", "resource": "vespene", "amount": 999}]},
    {"action": "wait", "any_of": [{"condition": "interval", "seconds": 2}],
     "all_of": [{"condition": "resource_at_least", "resource": "vespene", "amount": 999}]},
    {"action": "wait", "any_of": [{"condition": "interval", "seconds": 300}]},
    {"action": "wait"},
])
def test_fixed_one_minute_cap_overrides_wait_without_ending_episode(wait):
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset(EpisodeConfig(decision_interval_seconds=300, game_time_limit_seconds=None))
        for expected_time in (60, 120):
            obs, _, terminated, _ = env.step([wait])
            assert obs.game.game_time_seconds == expected_time
            assert not terminated and not obs.terminated
    finally:
        env.close()


def test_safety_cap_boundary_is_relative_to_each_step():
    backend = FakeBackend()
    trigger = trigger_from_wait(parse_decision([{
        "action": "wait", "all_of": [{"condition": "scan_ready"}],
    }]).wait, default_interval_seconds=5, max_game_time_seconds=None)
    backend.game_time_seconds = 159.99
    assert not trigger_satisfied(trigger, snapshot=backend.snapshot(), wait_started_at=100)
    backend.game_time_seconds = 160
    assert trigger_satisfied(trigger, snapshot=backend.snapshot(), wait_started_at=100)


def test_episode_limit_returns_before_one_minute_cap():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=20))
        obs, _, terminated, _ = env.step([{
            "action": "wait", "all_of": [{"condition": "scan_ready"}],
        }])
        assert terminated and obs.terminated
        assert obs.game.game_time_seconds == 20
    finally:
        env.close()
