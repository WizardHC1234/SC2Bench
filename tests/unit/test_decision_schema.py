"""Strict decision Schema / parser consistency and no-side-effect tests."""

from __future__ import annotations

import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.action_catalog import decision_json_schema
from sc2bench_env.interface.actions import ActionValidationError, parse_decision
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.decision_rules import (
    DecisionSchemaError,
    schema_accepts_entry,
    validate_batch_shape,
)


def _wait(seconds: float = 1.0) -> dict:
    return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}


@pytest.mark.parametrize(
    "payload",
    [
        [{"action": "build", "target": "supply_depot", "count": 1}, _wait()],
        [{"action": "build", "target": "barracks", "foo": 1}, _wait()],
        [{"action": "train", "target": "marine"}, _wait()],
        [{"action": "train", "target": "marine", "count": 0}, _wait()],
        [{"action": "train", "target": "marine", "count": 1, "route": ["zone_0"]}, _wait()],
        [{"action": "research", "target": "stimpack", "count": 1}, _wait()],
        [{"action": "call_mule", "target": "zone_0"}, _wait()],
        [{"action": "scout"}, _wait()],
        [{"action": "upgrade", "target": "barracks", "to": "orbital_command"}, _wait()],
        [{"action": "build", "target": "supply_depot", "action_id": "x"}, _wait()],
        [
            {"action": "build", "target": "supply_depot"},
            {
                "action": "wait",
                "any_of": [{"condition": "not_a_real_condition"}],
            },
        ],
        [
            {"action": "build", "target": "supply_depot"},
            {
                "action": "wait",
                "any_of": [
                    {
                        "condition": "resource_at_least",
                        "resource": "minerals",
                        "amount": 100,
                        "extra": True,
                    }
                ],
            },
        ],
        [
            {"action": "wait"},
            {"action": "build", "target": "barracks"},
            _wait(),
        ],
        [{"action": "build", "target": "supply_depot"}],
    ],
)
def test_illegal_decisions_rejected_by_parser_and_field_rules(payload) -> None:
    with pytest.raises(ActionValidationError):
        parse_decision(payload)
    with pytest.raises(DecisionSchemaError):
        validate_batch_shape(payload)


def test_schema_omits_action_id_and_forbids_additional_properties() -> None:
    schema = decision_json_schema()
    for branch in schema["items"]["oneOf"]:
        assert "action_id" not in branch.get("properties", {})
        assert branch.get("additionalProperties") is False
    assert schema["x-sc2bench-batch-rules"]["action_id_forbidden_in_model_json"] is True


def test_wait_condition_schema_is_closed() -> None:
    schema = decision_json_schema()
    wait_branch = next(
        branch for branch in schema["items"]["oneOf"] if branch["properties"]["action"]["const"] == "wait"
    )
    cond_schema = wait_branch["properties"]["any_of"]["items"]
    assert "oneOf" in cond_schema
    for branch in cond_schema["oneOf"]:
        assert branch.get("additionalProperties") is False
        assert "condition" in branch["required"]


def test_legal_decision_accepted() -> None:
    batch = parse_decision(
        [
            {"action": "build", "target": "supply_depot"},
            {"action": "train", "target": "marine", "count": 2},
            {"action": "call_mule"},
            {
                "action": "wait",
                "any_of": [
                    {"condition": "supply_left_at_most", "amount": 2},
                    {"condition": "scan_ready"},
                ],
                "all_of": [
                    {"condition": "building_count_at_least", "building": "barracks", "count": 1}
                ],
            },
        ]
    )
    assert len(batch.actions) == 3
    validate_batch_shape(batch.to_dicts())


def test_schema_accepts_entry_matches_unknown_field_rejection() -> None:
    bad = {"action": "build", "target": "barracks", "count": 1}
    assert schema_accepts_entry(bad) is not None
    with pytest.raises(ActionValidationError):
        parse_decision([bad, _wait()])


def test_invalid_batch_has_no_side_effects() -> None:
    backend = FakeBackend(mineral_income_per_second=0.0)
    env = Environment(backend)
    obs0 = env.reset(EpisodeConfig(decision_interval_seconds=5.0, game_time_limit_seconds=60.0))
    start_time = obs0.game_time_seconds
    before_demands = len(env.task_manager.active_demands())
    before_minerals = backend.minerals

    obs, feedback, terminated, info = env.step(
        [
            {"action": "build", "target": "supply_depot"},
            {"action": "train", "target": "marine", "count": 1, "extra": True},
            _wait(5),
        ]
    )
    assert not terminated
    assert feedback.events and feedback.events[0]["type"] == "decision_rejected"
    assert "error" in info
    assert len(env.task_manager.active_demands()) == before_demands
    assert obs.game_time_seconds == start_time
    assert backend.minerals == before_minerals
    assert obs.building.get("supply_depot", {}).get("waiting_to_start", 0) == 0
    env.close()


def test_build_rejects_typo_field() -> None:
    with pytest.raises(ActionValidationError, match="unknown fields"):
        parse_decision(
            [{"action": "build", "target": "barracks", "targte": "barracks"}, _wait()]
        )
