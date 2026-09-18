"""LLM-only analysis paragraph; environment still receives action arrays."""

import copy
import json
from unittest.mock import MagicMock

import pytest

from examples import agent_integration as example, agent_integration
from tests.helpers import legacy_llm_episode as legacy
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.platform_rules import DECISION_OUTPUT_RULE, OUTPUT_FORMAT_RULE


def test_direct_analysis_instruction_replaces_conflicts_without_examples_or_mutation():
    env = Environment(record_trajectory=False)
    try:
        env.reset()
        context = env.get_context()
        before = copy.deepcopy(context)
        adapted = example.llm_messages(context, decision_summary=True)
        text = "\n".join(message["content"] for message in adapted)
        assert example.SUMMARY_OUTPUT_RULE in adapted[0]["content"]
        assert example.SUMMARY_DECISION_REQUEST in adapted[1]["content"]
        assert "The paragraph is required" in text
        assert OUTPUT_FORMAT_RULE not in text and DECISION_OUTPUT_RULE not in text
        assert "Complete reply shape:" not in text
        assert "Action-array shape (after the summary paragraph):" in text
        assert context == before == env.get_context()
        for anchor in ("marine", "zone_1", '"count":', "Example:"):
            assert anchor not in example.SUMMARY_OUTPUT_RULE
        assert example.llm_messages(context, decision_summary=False) == before
    finally:
        env.close()


def test_prompt_adapter_fails_closed_when_contract_changes():
    with pytest.raises(ValueError, match="does not match"):
        example.llm_messages([{"role": "system", "content": "custom instructions"}], decision_summary=True)


def test_paragraph_and_nested_array_are_extracted_without_syntax_repair(monkeypatch):
    repair = MagicMock()
    monkeypatch.setattr(example, "repair_json", repair)
    summary = "The current requests remain active. I will wait for the observed state to change."
    array = '[{"action":"wait","any_of":[{"condition":"interval","seconds":10}]}]'
    decision, repaired, analysis = example.parse_model_reply(summary + "\n\n" + array, decision_summary=True)
    assert decision == json.loads(array) and repaired is False and analysis == summary
    repair.assert_not_called()


def test_brackets_in_quoted_values_do_not_end_extraction_early():
    summary = "I choose the next action from current facts."
    array = '[{"action":"train","target":"a]b\\\"c[","count":1},{"action":"wait"}]'
    decision, repaired, analysis = example.parse_model_reply(summary + "\n\n" + array, decision_summary=True)
    assert decision == json.loads(array) and repaired is False and analysis == summary


@pytest.mark.parametrize("suffix", [
    '[{"action":"wait",}]',
    "[{'action':'wait'},]",
    '```json\n[{"action":"wait",}]\n```',
])
def test_only_array_syntax_is_repaired_not_the_analysis(suffix):
    decision, repaired, analysis = example.parse_model_reply("Current work continues.\n\n" + suffix, decision_summary=True)
    assert decision == [{"action": "wait"}]
    assert repaired is True and analysis == "Current work continues."


@pytest.mark.parametrize("text", [
    'Analysis only, without actions.',
    'Analysis.\n\n[{"action":"wait"}',
    'Analysis.\n\n[{"action":"wait"}]\n[{"action":"wait"}]',
    'Analysis.\n\n[{"action":"wait"}]\nextra explanation',
    '[{"action":"wait"}]\n[{"action":"wait"}]',
    '{"actions":\n[{"action":"wait"}]}',
])
def test_ambiguous_or_incomplete_output_is_not_salvaged(text):
    with pytest.raises(ValueError):
        example.parse_model_reply(text, decision_summary=True)


def test_records_separate_analysis_from_api_reasoning_and_submitted_actions(tmp_path):
    env = Environment(record_dir=tmp_path)
    raw = 'Current accepted work remains active. I will wait.\n\n[{"action":"wait"}]'
    calls = []

    def call(messages):
        calls.append(messages)
        return {"content": raw, "raw_content": raw, "reasoning": "", "thinking_requested": False}

    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=1))
        result = legacy.run_episode(env, call, verbose=False)
        assert result["result"] == "Result.Tie"
        from sc2bench_env.recording.reader import read_episode
        entry = read_episode(env.record_path)["interactions"][0]
        assert entry["input"]["messages"] == calls[0]
        assert entry["output"]["assistant_content"] == raw
        assert entry["output"]["submitted_decision"] == [{"action": "wait"}]
        assert entry["metadata"]["decision_summary"] == raw.split("\n\n")[0]
        assert entry["metadata"]["decision_summary_requested"] is True
        assert entry["metadata"]["reasoning"] == ""
        assert entry["metadata"]["thinking_requested"] is False
        assert read_episode(env.record_path)["platform_prompt"] == env.get_system_prompt()
        assert calls[0][0]["content"] != env.get_system_prompt()
    finally:
        env.close()


def test_invalid_actions_still_reject_and_correction_keeps_paragraph_instruction(tmp_path):
    env = Environment(record_dir=tmp_path)
    calls = []

    def call(messages):
        calls.append(messages)
        array = '[{"action":"build","target":"barracks","count":2},{"action":"wait"}]' if len(calls) == 1 else '[{"action":"wait"}]'
        return {"content": "I choose based on current facts.\n\n" + array}

    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=1))
        legacy.run_episode(env, call, verbose=False)
        steps = [row for row in env.trajectory()["steps"] if row.get("type") == "step"]
        assert steps[0]["game_time_after_seconds"] == steps[0]["game_time_before_seconds"] == 0
        assert example.SUMMARY_DECISION_REQUEST in calls[1][-1]["content"]
        assert "Do not use Markdown fences or prose." not in calls[1][-1]["content"]
        assert not env.task_manager.active_demands()
    finally:
        env.close()


@pytest.mark.parametrize("flags, expected", [([], True), (["--no-decision-summary"], False), (["--decision-summary"], True)])
def test_cli_passes_output_mode_to_runner(monkeypatch, flags, expected):
    monkeypatch.setattr("sys.argv", ["agent_integration.py", *flags])
    monkeypatch.setattr(example, "resolve_api_key", lambda: "test-key")
    monkeypatch.setattr(example, "make_llm_call", MagicMock())
    runner = MagicMock()
    runner.run.return_value = {"summary_path": "run.json", "episodes": [],
                               "aggregate": {}, "termination_counts": {}}
    monkeypatch.setattr(agent_integration, "BenchmarkRunner", MagicMock(return_value=runner))
    example.main()
    factory = runner.run.call_args.kwargs["agent_factory"]
    assert factory().decision_summary is expected
