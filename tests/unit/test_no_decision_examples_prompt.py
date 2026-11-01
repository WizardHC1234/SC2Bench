"""Generic syntax templates must not prescribe concrete game decisions."""
from agents.llm_agent.prompts import example_prompt_parts
from sc2bench_env.adapters.llm import LLMAdapter
from agents.llm_agent.prompts import platform_turn_messages

import json

from sc2bench_env import Environment
from sc2bench_env.catalog import registry as catalog
from sc2bench_env.interface.actions import parse_decision
from sc2bench_env.interface.decision_rules import decision_json_schema
from sc2bench_env.interface.prompt_fields import (
    COMMAND_TEMPLATES, FORMAT_HEADER, NUMERIC_PLACEHOLDER_RE,
)
from sc2bench_env.recording.reader import read_episode


def test_fixed_prompt_does_not_inject_templates_or_concrete_choices():
    prompt = catalog.render_system_prompt()
    assert FORMAT_HEADER.rstrip() not in prompt
    for template in COMMAND_TEMPLATES.values():
        assert template not in prompt
    assert '[{"action":' not in prompt
    for example in catalog.action_syntax_examples():
        if example not in ({"name": "advance", "arguments": {"seconds": 20}}, {"name": "call_mule", "arguments": {}}):
            assert json.dumps(example, separators=(",", ":")) not in prompt
    for identifier in ("zone_1", "zone_2", "zone_3", "zone_10", "townhall_0", "group_1", "group_2"):
        assert identifier not in prompt


def test_prompt_does_not_read_documentation_example_helpers(monkeypatch):
    def forbidden():
        raise AssertionError("Documentation examples must not feed the prompt")
    monkeypatch.setattr(catalog, "action_syntax_examples", forbidden)
    monkeypatch.setattr(catalog, "decision_examples", forbidden)
    catalog.render_system_prompt()


def test_generic_object_templates_parse_after_typed_placeholder_substitution():
    replacements = {
        "<building_or_addon_name>": "supply_depot", "<unit_name>": "marine",
        "<research_name>": "stimpack", "<build_or_train_or_research>": "train",
        "<build_or_train_or_research_or_morph_townhall>": "train",
        "<target_name>": "marine", "<townhall_id>": "townhall_0", "<morph_name>": "orbital_command",
        "<zone_id>": "zone_2", "<another_zone_id>": "zone_4",
        "<attack_or_defend>": "attack", "<outbound_group_id>": "group_2",
        "<move_or_recall>": "move",
        "<positive_integer>": "7", "<optional_positive_integer>": "2",
        "<positive_number>": "13.5",
    }
    assert len(COMMAND_TEMPLATES) == 19
    for template in COMMAND_TEMPLATES.values():
        text = template
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        text = NUMERIC_PLACEHOLDER_RE.sub("1", text)
        entry = json.loads(text)
        batch = [entry] if entry["name"] == "advance" else [entry, {
            "name": "advance", "arguments": {"seconds": 13.5},
        }]
        race = {"chrono_boost": "protoss", "inject_larva": "zerg", "spawn_creep_tumor": "zerg"}.get(entry["name"], "terran")
        assert parse_decision(batch, race=race).to_dicts() == batch


def test_noop_remains_a_legal_complete_reply():
    batch = parse_decision([{"name": "advance", "arguments": {"seconds": 5}}])
    assert not batch.actions
    assert batch.advance.seconds == 5


def test_all_names_costs_and_documentation_examples_remain_available():
    before = catalog.catalog_as_dicts()
    catalog.render_system_prompt()
    assert catalog.catalog_as_dicts() == before
    schema = decision_json_schema()
    assert schema["examples"] == list(catalog.decision_examples())
    for example in schema["examples"]:
        assert parse_decision(example).to_dicts() == example
    assert len(catalog.targets_for_action("train")) == 17
    assert len(catalog.targets_for_action("research")) == 31


def test_actual_context_and_export_share_the_same_prompt(tmp_path):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset()
        prompt = LLMAdapter(race=env.config.race if env.config else "terran").system_prompt()
        assert platform_turn_messages(env.latest_observation, env.latest_feedback, env.config.race if env.config else "terran")[0]["content"] == LLMAdapter(example_prompt_parts("terran"), race="terran").system_prompt()
        assert read_episode(env.record_path)["platform_prompt"] == prompt
        assert FORMAT_HEADER.rstrip() not in prompt
    finally:
        env.close()
