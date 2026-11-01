"""Whole-match planning without a prescribed build, mixture or model setting."""
from sc2bench_env.adapters.llm import LLMAdapter
from agents.llm_agent.prompts import platform_turn_messages
import json

from sc2bench_env import Environment
from sc2bench_env.catalog.registry import action_syntax_examples, decision_examples, render_system_prompt
from sc2bench_env.interface.decision_rules import decision_json_schema
from sc2bench_env.interface.platform_rules import ACTION_RULES, PLANNING_RULES
from sc2bench_env.recording.reader import read_episode


def test_planning_rules_present_once_and_before_execution_details():
    prompt = render_system_prompt()
    assert prompt.count(PLANNING_RULES) == 1
    assert prompt.index("5. Platform execution model") < prompt.index(PLANNING_RULES)
    assert prompt.index(PLANNING_RULES) < prompt.index("Execution model:")
    for phrase in ["same continuing match", "persistent accepted work", "continuing income",
                   "supply, prerequisites, completion times and existing unfinished work"]:
        assert phrase in PLANNING_RULES


def test_no_forced_mixture_transition_or_public_reasoning():
    prompt = render_system_prompt()
    for forbidden in (
        "required composition", "required technology transition", "reasoning paragraph",
        "Return only a JSON array", "No pre-defined strategy",
    ):
        assert forbidden not in prompt
    for target in ("marine", "marauder", "siege_tank", "battlecruiser"):
        assert target not in PLANNING_RULES


def test_training_examples_remain_schema_only_not_model_decisions():
    examples = decision_examples()
    targets = [
        row["arguments"]["target"] for example in examples for row in example
        if row["name"] == "train"
    ]
    assert len(set(targets)) >= 3
    assert len(targets) == len(set(targets))  # No repeatedly favoured training target.
    assert "scv" in targets and any(target != "scv" for target in targets)
    assert decision_json_schema()["examples"] == list(examples)
    for example in action_syntax_examples():
        if example not in ({"name": "advance", "arguments": {'seconds': 20}}, {"name": "call_mule", "arguments": {}}):
            assert json.dumps(example, separators=(",", ":")) not in render_system_prompt()


def test_incremental_rule_has_no_unit_anchor_and_preserves_semantics():
    assert "count is the minimum number of additional target units" in ACTION_RULES["train"]
    assert "stays active until that minimum is met" in ACTION_RULES["train"]
    assert "marine" not in ACTION_RULES["train"]


def test_current_prompt_context_and_new_record_use_same_planning_rules(tmp_path):
    from agents.llm_agent.prompts import example_prompt_parts

    env = Environment(record_dir=tmp_path)
    try:
        env.reset()
        race = env.config.race if env.config else "terran"
        platform_prompt = LLMAdapter(race=race).system_prompt()
        example_prompt = LLMAdapter(example_prompt_parts(race), race=race).system_prompt()
        assert platform_turn_messages(
            env.latest_observation, env.latest_feedback, race,
        )[0]["content"] == example_prompt
        assert read_episode(env.record_path)["platform_prompt"] == platform_prompt
        assert PLANNING_RULES in platform_prompt
    finally:
        env.close()
