"""Reviewed aliases do not relax the canonical action contract."""

import copy
import json

import pytest

from sc2bench_env import Environment
from sc2bench_env.interface.action_catalog import decision_json_schema, render_system_prompt
from sc2bench_env.interface.actions import (
    ActionValidationError, attach_retry_ids, parse_decision, parse_game_action,
)
from sc2bench_env.interface.decision_rules import DecisionSchemaError, validate_batch_shape
from sc2bench_env.interface.feedback import Feedback
from sc2bench_env.interface.observation_text import render_feedback_text
from sc2bench_env.interface.target_aliases import TARGET_ALIASES
from sc2bench_env.recording.context import platform_messages


def decision(target="stim_pack"):
    return [{"action": "research", "target": target}, {"action": "wait"}]


@pytest.mark.parametrize("target", ["stim_pack", " STIM_PACK "])
def test_alias_canonicalizes_without_mutating_original(target):
    raw = decision(target)
    original = copy.deepcopy(raw)
    batch = parse_decision(raw)
    assert raw == original
    assert batch.raw == tuple(original)
    assert batch.actions[0].target == "stimpack"
    assert batch.to_dicts()[0]["target"] == "stimpack"
    assert batch.normalizations == ({"entry_index": 0, "action": "research",
                                     "field": "target", "original": target,
                                     "canonical": "stimpack"},)
    assert parse_game_action(raw[0]).target == "stimpack"
    validate_batch_shape(batch.to_dicts())


def test_canonical_name_and_existing_case_convention_need_no_alias_audit():
    for target in ("stimpack", " Stimpack "):
        assert parse_decision(decision(target)).normalizations == ()


def test_cancel_uses_same_alias_and_index_is_original_batch_position():
    raw = [{"action": "call_mule"},
           {"action": "cancel", "target_action": "research", "target": "stim_pack"},
           {"action": "wait"}]
    batch = parse_decision(raw)
    assert batch.actions[1].target == "stimpack"
    assert batch.normalizations[0]["entry_index"] == 1
    assert batch.normalizations[0]["target_action"] == "research"
    assert raw[1]["target"] == "stim_pack"


@pytest.mark.parametrize("target", ["stim_pak", "stim-pack", "stim pack", "stim_packs"])
def test_no_fuzzy_name_correction(target):
    with pytest.raises(ActionValidationError):
        parse_decision(decision(target))


@pytest.mark.parametrize("entry", [
    {"action": "train", "target": "stim_pack", "count": 1},
    {"action": "build", "target": "stim_pack"},
    {"action": "research", "target": "stim_pack", "count": 1},
    {"action": "research", "target": "stim_pack", "action_id": "agent-owned"},
    {"action": "research", "target": "stim_pack", "unknown": True},
    {"action": "research", "target": ["stim_pack"]},
])
def test_alias_does_not_relax_types_actions_or_allowed_fields(entry):
    with pytest.raises(ActionValidationError):
        parse_decision([entry, {"action": "wait"}])


@pytest.mark.parametrize("raw", [None, "stim_pack", [], [None, {"action": "wait"}],
                                 [{"action": "research", "target": "stim_pack"}]])
def test_malformed_batch_still_rejected(raw):
    with pytest.raises(ActionValidationError):
        parse_decision(raw)


def test_retry_ids_keep_alias_audit_and_original_submission():
    batch = parse_decision(decision())
    attached = attach_retry_ids(batch, ["harness-retry"])
    assert attached.actions[0].action_id == "harness-retry"
    assert attached.raw == batch.raw
    assert attached.normalizations == batch.normalizations


def test_schema_catalog_and_strict_validator_remain_canonical():
    schema = json.dumps(decision_json_schema())
    assert '"stimpack"' in schema
    assert '"stim_pack"' not in schema
    assert "stim_pack" not in render_system_prompt()
    with pytest.raises(DecisionSchemaError):
        validate_batch_shape(decision())
    for verb, aliases in TARGET_ALIASES.items():
        for alias, canonical in aliases.items():
            assert parse_game_action({"action": verb, "target": canonical}).target == canonical
            assert alias != canonical


def test_alias_registry_is_read_only():
    with pytest.raises(TypeError):
        TARGET_ALIASES["research"]["guessed"] = "stimpack"


def test_empty_feedback_contract_is_unchanged():
    assert Feedback().to_dict() == {"receipts": [], "events": []}
    assert "Name normalizations" not in render_feedback_text(Feedback().to_dict())


def test_env_alias_record_feedback_and_actual_raw_reply(tmp_path):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset()
        raw = decision()
        assistant = json.dumps(raw)
        obs, feedback, _, _ = env.step(raw, agent_context={
            "messages": [{"role": "user", "content": "actual input"}],
            "assistant_content": assistant,
        })
        assert feedback.receipts[0].target == "stimpack"
        assert feedback.receipts[0].result != "rejected"
        assert feedback.events == obs.recent_events
        serialized = feedback.to_dict()
        assert serialized["name_normalizations"][0]["original"] == "stim_pack"
        text = platform_messages(env.get_system_prompt(), obs.to_dict(), serialized)[1]["content"]
        assert "stim_pack -> stimpack" in text
        record = env.trajectory()["steps"][-1]
        assert record["validation"]["accepted"] is True
        assert record["submitted_decision"] == raw
        assert record["parsed_decision"][0]["target"] == "stimpack"
        assert record["feedback"]["name_normalizations"] == serialized["name_normalizations"]
        from sc2bench_env.recording.reader import read_episode
        interactions = read_episode(env.record_path)
        assert interactions["interactions"][0]["output"]["assistant_content"] == assistant
        assert interactions["interactions"][0]["output"]["submitted_decision"] == raw
        # Canonical and alias submissions address the same research request.
        demands = len(env.task_manager.active_demands())
        _, repeated, _, _ = env.step(decision("stimpack"))
        assert len(env.task_manager.active_demands()) == demands
        assert "name_normalizations" not in repeated.to_dict()
        _, cancelled, _, _ = env.step([
            {"action": "cancel", "target_action": "research", "target": "stim_pack"},
            {"action": "wait"},
        ])
        assert cancelled.receipts[0].target == "stimpack"
        assert not env.task_manager.active_demands()
    finally:
        env.close()


def test_whole_batch_rejection_does_not_apply_or_report_alias(tmp_path):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset()
        raw = [decision()[0], {"action": "train", "target": "marine", "count": True},
               {"action": "wait"}]
        obs, feedback, _, _ = env.step(raw)
        assert not env.task_manager.active_demands()
        assert not feedback.name_normalizations
        record = env.trajectory()["steps"][-1]
        assert record["submitted_decision"] == raw
        assert record["parsed_decision"] == []
        assert record["validation"]["accepted"] is False
        assert record["game_time_before_seconds"] == record["game_time_after_seconds"]
    finally:
        env.close()


def test_example_next_actual_request_explains_alias_without_rejection(tmp_path):
    from tests.helpers.legacy_llm_episode import run_episode
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment(record_dir=tmp_path)
    seen = []
    raw = [{"action": "research", "target": "stim_pack"},
           {"action": "wait", "any_of": [{"condition": "interval", "seconds": 1}]}]

    def mock_llm(messages):
        seen.append(messages)
        content = json.dumps(raw if len(seen) == 1 else [{"action": "wait"}])
        return {"content": content, "raw_content": content, "model": "mock"}

    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=2))
        run_episode(env, mock_llm)
        assert len(seen) == 2
        assert "stim_pack -> stimpack" in seen[1][1]["content"]
        assert not any("Rejected decision" in row["content"] for row in seen[1])
        assert env.trajectory()["steps"][-2]["validation"]["accepted"] is True
    finally:
        env.close()
