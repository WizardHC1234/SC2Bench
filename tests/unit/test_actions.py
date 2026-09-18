"""Action parsing aligned with PLATFORM_PLAN flat JSON decisions."""

from __future__ import annotations

import pytest

from sc2bench_env.interface.actions import (
    ActionValidationError,
    parse_decision,
    parse_game_action,
)


def test_parse_build_without_count() -> None:
    action = parse_game_action({"action": "build", "target": "Barracks"})
    assert action.action == "build"
    assert action.target == "barracks"
    assert action.count == 1


def test_reject_build_with_count() -> None:
    with pytest.raises(ActionValidationError):
        parse_game_action({"action": "build", "target": "barracks", "count": 1})


def test_parse_train_and_research() -> None:
    train = parse_game_action({"action": "train", "target": "marine", "count": 8})
    research = parse_game_action({"action": "research", "target": "Stimpack"})
    assert train.count == 8
    assert research.target == "stimpack"
    assert research.count == 1


def test_decision_requires_trailing_wait() -> None:
    with pytest.raises(ActionValidationError):
        parse_decision([{"action": "build", "target": "supply_depot"}])


def test_parse_decision_batch() -> None:
    batch = parse_decision(
        [
            {"action": "build", "target": "supply_depot"},
            {"action": "build", "target": "barracks"},
            {"action": "train", "target": "marine", "count": 8},
            {
                "action": "wait",
                "any_of": [
                    {"condition": "resource_at_least", "resource": "minerals", "amount": 400}
                ],
            },
        ]
    )
    assert len(batch.actions) == 3
    assert batch.wait.any_of[0].condition == "resource_at_least"


def test_parse_cancel_and_scan() -> None:
    cancel = parse_game_action(
        {"action": "cancel", "target_action": "build", "target": "barracks"}
    )
    scan = parse_game_action({"action": "scan", "target": "zone_3"})
    assert cancel.target_action == "build"
    assert scan.target == "zone_3"


def test_reject_wait_in_middle() -> None:
    with pytest.raises(ActionValidationError):
        parse_decision(
            [
                {"action": "wait"},
                {"action": "build", "target": "barracks"},
                {"action": "wait"},
            ]
        )
