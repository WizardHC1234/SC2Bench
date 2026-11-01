"""Action parsing aligned with PLATFORM_PLAN flat JSON decisions."""
from __future__ import annotations


import pytest

from sc2bench_env.interface.actions import (
    ActionValidationError,
    parse_decision,
    parse_game_action,
)


def test_parse_build_without_count() -> None:
    action = parse_game_action({"name": "build", "arguments": {'target': "Barracks"}})
    assert action.action == "build"
    assert action.target == "barracks"
    assert action.count == 1


def test_reject_build_with_count() -> None:
    with pytest.raises(ActionValidationError):
        parse_game_action({"name": "build", "arguments": {'target': "barracks", 'count': 1}})


def test_parse_train_and_research() -> None:
    train = parse_game_action({"name": "train", "arguments": {'target': "marine", 'count': 8}})
    research = parse_game_action({"name": "research", "arguments": {'target': "Stimpack"}})
    assert train.count == 8
    assert research.target == "stimpack"
    assert research.count == 1


def test_decision_requires_trailing_wait() -> None:
    with pytest.raises(ActionValidationError):
        parse_decision([{"name": "build", "arguments": {'target': "supply_depot"}}])


def test_parse_decision_batch() -> None:
    batch = parse_decision(
        [
            {"name": "build", "arguments": {'target': "supply_depot"}},
            {"name": "build", "arguments": {'target': "barracks"}},
            {"name": "train", "arguments": {'target': "marine", 'count': 8}},
            {"name": "advance", "arguments": {'seconds': 20}},
        ]
    )
    assert len(batch.actions) == 3
    assert batch.advance.seconds == 20


def test_parse_cancel_and_scan() -> None:
    cancel = parse_game_action(
        {"name": "cancel", "arguments": {'target_action': "build", 'target': "barracks"}}
    )
    scan = parse_game_action({"name": "scan", "arguments": {'target': "zone_3"}})
    assert cancel.target_action == "build"

    partial = parse_game_action(
        {"name": "cancel", "arguments": {
            "target_action": "train", "target": "marine", "count": 3,
        }}
    )
    assert partial.count == 3
    assert scan.target == "zone_3"


def test_cancel_requires_target_action_and_target() -> None:
    with pytest.raises(ActionValidationError, match="missing required fields"):
        parse_game_action({"name": "cancel", "arguments": {}})
    with pytest.raises(ActionValidationError, match="unknown fields"):
        parse_game_action({"name": "cancel", "arguments": {"task_id": "stable-task-id"}})
    with pytest.raises(ActionValidationError, match="positive integer"):
        parse_game_action({"name": "cancel", "arguments": {
            "target_action": "train", "target": "marine", "count": 0,
        }})


def test_reject_wait_in_middle() -> None:
    with pytest.raises(ActionValidationError):
        parse_decision(
            [
                {"name": "advance", "arguments": {'seconds': 5}},
                {"name": "build", "arguments": {'target': "barracks"}},
                {"name": "advance", "arguments": {'seconds': 5}},
            ]
        )
