"""Strict decision Schema / parser consistency and no-side-effect tests."""
from __future__ import annotations


from tests.helpers.drive import drive
import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.decision_rules import decision_json_schema
from sc2bench_env.interface.actions import ActionValidationError, parse_decision
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.decision_rules import (
    DecisionSchemaError,
    schema_accepts_entry,
    validate_batch_shape,
)


def _wait(seconds: float = 1.0) -> dict:
    return {"name": "advance", "arguments": {'seconds': seconds}}


@pytest.mark.parametrize(
    "payload",
    [
        [{"name": "build", "arguments": {'target': "supply_depot", 'count': 1}}, _wait()],
        [{"name": "build", "arguments": {'target': "barracks", 'foo': 1}}, _wait()],
        [{"name": "train", "arguments": {'target': "marine"}}, _wait()],
        [{"name": "train", "arguments": {'target': "marine", 'count': 0}}, _wait()],
        [{"name": "train", "arguments": {'target': "marine", 'count': 1, 'route': ["zone_0"]}}, _wait()],
        [{"name": "research", "arguments": {'target': "stimpack", 'count': 1}}, _wait()],
        [{"name": "call_mule", "arguments": {'target': "zone_0"}}, _wait()],
        [{"name": "scout", "arguments": {}}, _wait()],
        [{"name": "morph_townhall", "arguments": {'target': "barracks", 'to': "orbital_command"}}, _wait()],
        [{"name": "build", "arguments": {'target': "supply_depot", 'action_id': "x"}}, _wait()],
        [
            {"name": "build", "arguments": {'target': "supply_depot"}},
            {
                "action": "wait",
                "any_of": [{"condition": "not_a_real_condition"}],
            },
        ],
        [
            {"name": "build", "arguments": {'target': "supply_depot"}},
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
            {"name": "advance", "arguments": {'seconds': 5}},
            {"name": "build", "arguments": {'target': "barracks"}},
            _wait(),
        ],
        [{"name": "build", "arguments": {'target': "supply_depot"}}],
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


def test_advance_schema_requires_positive_seconds() -> None:
    schema = decision_json_schema()
    advance_branch = next(
        branch for branch in schema["items"]["oneOf"]
        if branch["properties"]["name"]["const"] == "advance"
    )
    assert advance_branch["required"] == ["name", "arguments"]
    assert advance_branch["properties"]["arguments"]["properties"]["seconds"]["exclusiveMinimum"] == 0
    assert advance_branch.get("additionalProperties") is False
    assert "wait" not in {
        branch["properties"]["name"]["const"] for branch in schema["items"]["oneOf"]
    }


def test_legal_decision_accepted() -> None:
    batch = parse_decision(
        [
            {"name": "build", "arguments": {'target': "supply_depot"}},
            {"name": "train", "arguments": {'target': "marine", 'count': 2}},
            {"name": "call_mule", "arguments": {}},
            {"name": "advance", "arguments": {'seconds': 15}},
        ]
    )
    assert len(batch.actions) == 3
    assert batch.advance.seconds == 15
    validate_batch_shape([
        {"action": item["name"], **item["arguments"]} for item in batch.to_dicts()
    ])


def test_schema_accepts_entry_matches_unknown_field_rejection() -> None:
    bad = {"name": "build", "arguments": {'target': "barracks", 'count': 1}}
    assert schema_accepts_entry(bad) is not None
    with pytest.raises(ActionValidationError):
        parse_decision([bad, _wait()])


def test_invalid_batch_has_no_side_effects() -> None:
    backend = FakeBackend(mineral_income_per_second=0.0)
    env = Environment(backend)
    obs0 = env.reset(EpisodeConfig(game_time_limit_seconds=60.0))
    start_time = obs0.game_time_seconds
    before_demands = len(env.task_manager.active_demands())
    before_minerals = backend.minerals

    obs, feedback, terminated, info = drive(env,
        [
            {"name": "build", "arguments": {'target': "supply_depot"}},
            {"name": "train", "arguments": {'target': "marine", 'count': 1, 'extra': True}},
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
            [{"name": "build", "arguments": {'target': "barracks", 'targte': "barracks"}}, _wait()]
        )
