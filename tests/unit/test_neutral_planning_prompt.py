"""Whole-match planning without a prescribed build, mixture or model setting."""
import json

from sc2bench_env import Environment
from sc2bench_env.interface.action_catalog import action_syntax_examples, decision_examples, render_system_prompt
from sc2bench_env.interface.decision_rules import decision_json_schema
from sc2bench_env.interface.platform_rules import ACTION_RULES, PLANNING_RULES
from sc2bench_env.recording.reader import read_episode


def test_planning_rules_present_once_and_before_execution_details():
    prompt = render_system_prompt()
    assert prompt.count(PLANNING_RULES) == 1
    assert prompt.index("Player responsibility:") < prompt.index("2. Interaction protocol")
    for phrase in ["whole-match plan", "ready producer slots", "observed enemy capabilities",
                   "If keeping blocked work", "supply missing prerequisites yourself", "Longer request lists alone"]:
        assert phrase in prompt


def test_no_forced_mixture_transition_or_public_reasoning():
    prompt = render_system_prompt()
    assert "Single-unit and mixed armies are both allowed" in prompt
    assert "no prescribed composition or required technology transition" in prompt
    assert "Unknown enemy information is not evidence for a counter" in prompt
    assert "No prose, reasoning paragraph" in prompt
    assert "Return only a JSON array" in prompt
    assert "No pre-defined strategy" not in prompt  # No hidden external strategy default.
    for target in ("marine", "marauder", "siege_tank", "battlecruiser"):
        assert target not in PLANNING_RULES


def test_training_examples_remain_schema_only_not_model_decisions():
    examples = decision_examples()
    targets = [row["target"] for example in examples for row in example if row["action"] == "train"]
    assert len(set(targets)) >= 3
    assert len(targets) == len(set(targets))  # No repeatedly favoured training target.
    assert "scv" in targets and any(target != "scv" for target in targets)
    assert decision_json_schema()["examples"] == list(examples)
    for example in action_syntax_examples():
        if example not in ({"action": "wait"}, {"action": "call_mule"}):
            assert json.dumps(example, separators=(",", ":")) not in render_system_prompt()


def test_incremental_rule_has_no_unit_anchor_and_preserves_semantics():
    assert "train count=N requests N additional births" in ACTION_RULES["train"]
    assert "another identical request adds N more, not a target total" in ACTION_RULES["train"]
    assert "marine" not in ACTION_RULES["train"]


def test_current_prompt_context_and_new_record_use_same_planning_rules(tmp_path):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset()
        prompt = env.get_system_prompt()
        assert env.get_context()[0]["content"] == prompt
        assert read_episode(env.record_path)["platform_prompt"] == prompt
        assert PLANNING_RULES in prompt
    finally:
        env.close()
