"""One action-local reference, with complete metadata and unchanged exports."""
from sc2bench_env.adapters.llm import LLMAdapter
from agents.llm_agent.prompts import example_prompt_parts, platform_turn_messages
import copy

import pytest

from sc2bench_env import Environment
from sc2bench_env.catalog.registry import (
    catalog_as_dicts, render_action_catalog,
    render_system_prompt, targets_for_action,
)
from sc2bench_env.interface.decision_rules import decision_json_schema
from sc2bench_env.interface.platform_rules import ACTION_RULES, GAME_RULES
from sc2bench_env.interface.prompt_fields import COMMAND_TEMPLATES
from sc2bench_env.recording.reader import read_episode


def chapters():
    prompt = render_system_prompt()
    execution = prompt.split("5. Platform execution model\n", 1)[1].split("\n\n6. Decision guidance\n", 1)[0]
    basics = prompt.split("2. Game basics\n", 1)[1].split("\n\n3. Reading Observation\n", 1)[0]
    return prompt, execution, basics


@pytest.mark.parametrize("verb", ["build", "train", "research", "morph_townhall", "scout", "scan", "call_mule", "supply_drop"])
def test_target_reference_and_action_semantics_stay_out_of_the_system_prompt(verb):
    prompt, execution, basics = chapters()
    standalone = render_action_catalog()
    block = standalone.split(f"[{verb}]\n", 1)[1].split("\n\n", 1)[0]
    assert block in standalone
    assert block not in prompt
    assert ACTION_RULES[verb] not in execution
    assert COMMAND_TEMPLATES[verb] not in execution
    assert ACTION_RULES[verb] not in basics
    assert f"[{verb}]" in standalone


def test_game_basics_contains_rules_and_background_not_another_catalog():
    prompt, execution, basics = chapters()
    assert basics == GAME_RULES.rstrip()
    assert "Action Catalog" not in prompt
    assert "Target tables (terran):" not in execution
    assert "cost minerals/vespene" not in basics


def test_target_notes_follow_the_relevant_table_not_the_last_chapter():
    _, _, basics = chapters()
    catalog = render_action_catalog()
    assert "supply_depot provides +8 supply when ready" in catalog
    assert "Target notes:" in catalog
    assert "Target notes:" not in basics


def test_rendering_does_not_change_metadata_schema_or_action_semantics():
    before = copy.deepcopy((catalog_as_dicts(), decision_json_schema(), ACTION_RULES, COMMAND_TEMPLATES))
    render_system_prompt()
    render_action_catalog()
    assert (catalog_as_dicts(), decision_json_schema(), ACTION_RULES, COMMAND_TEMPLATES) == before


def test_environment_and_record_use_platform_prompt_while_example_replaces_guidance(tmp_path):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset()
        prompt = render_system_prompt()
        assert LLMAdapter(race=env.config.race if env.config else "terran").system_prompt() == prompt
        race = env.config.race if env.config else "terran"
        example_prompt = platform_turn_messages(
            env.latest_observation, env.latest_feedback, race,
        )[0]["content"]
        assert example_prompt == LLMAdapter(example_prompt_parts(race), race=race).system_prompt()
        assert example_prompt != prompt
        assert read_episode(env.record_path)["platform_prompt"] == prompt
        assert "Action Catalog" not in prompt
    finally:
        env.close()
