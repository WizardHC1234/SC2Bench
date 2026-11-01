"""advance(seconds) is the only time-advance action; wait is rejected."""
from __future__ import annotations


from tests.helpers.drive import drive
import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.actions import ActionValidationError, parse_decision
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.tools import LEGACY_ACTION_FORMAT_ERROR


def test_reject_legacy_action_format() -> None:
    with pytest.raises(ActionValidationError, match="legacy"):
        parse_decision(
            [
                {"action": "build", "target": "barracks"},
                {"action": "advance", "seconds": 5},
            ]
        )


def test_reject_wait() -> None:
    with pytest.raises(ActionValidationError, match="wait is no longer supported"):
        parse_decision([{"name": "wait", "arguments": {}}])


def test_reject_missing_or_non_positive_seconds() -> None:
    with pytest.raises(ActionValidationError):
        parse_decision([{"name": "advance", "arguments": {}}])
    with pytest.raises(ActionValidationError):
        parse_decision([{"name": "advance", "arguments": {"seconds": 0}}])
    with pytest.raises(ActionValidationError):
        parse_decision([{"name": "advance", "arguments": {"seconds": -1}}])


def test_parse_advance() -> None:
    batch = parse_decision([{"name": "advance", "arguments": {"seconds": 7}}])
    assert batch.advance.seconds == 7
    assert batch.to_dicts() == [{"name": "advance", "arguments": {"seconds": 7}}]


def test_fake_advances_requested_seconds() -> None:
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=30))
        observation, _, _, _ = drive(env,
            [{"name": "advance", "arguments": {"seconds": 8}}]
        )
        assert observation.game.game_time_seconds == 8
    finally:
        env.close()


def test_legacy_error_mentions_name_and_arguments() -> None:
    assert "name" in LEGACY_ACTION_FORMAT_ERROR
    assert "arguments" in LEGACY_ACTION_FORMAT_ERROR
