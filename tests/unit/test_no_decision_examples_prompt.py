"""Generic syntax templates must not prescribe concrete game decisions."""

import json

from sc2bench_env import Environment
from sc2bench_env.interface import action_catalog as catalog
from sc2bench_env.interface.actions import parse_decision
from sc2bench_env.interface.prompt_fields import FORMAT_TEMPLATES, FORMAT_HEADER, COMMAND_TEMPLATES
from sc2bench_env.recording.reader import read_episode


def test_only_generic_templates_and_no_concrete_choices_are_in_fixed_prompt():
    prompt = catalog.render_system_prompt()
    assert FORMAT_HEADER.rstrip() in prompt
    for template in COMMAND_TEMPLATES.values():
        assert prompt.count(template) == 1
    assert prompt.count('[{"action":"wait"}]') == 1
    for example in catalog.action_syntax_examples():
        if example not in ({"action": "wait"}, {"action": "call_mule"}):
            assert json.dumps(example, separators=(",", ":")) not in prompt
    for example in catalog.wait_condition_examples():
        assert json.dumps(example, separators=(",", ":")) not in prompt
    for identifier in ("zone_1", "zone_2", "zone_3", "zone_10", "cc_0", "group_1", "group_2"):
        assert identifier not in prompt


def test_prompt_does_not_read_documentation_example_helpers(monkeypatch):
    def forbidden():
        raise AssertionError("Documentation examples must not feed the prompt")
    monkeypatch.setattr(catalog, "action_syntax_examples", forbidden)
    monkeypatch.setattr(catalog, "wait_condition_examples", forbidden)
    monkeypatch.setattr(catalog, "decision_examples", forbidden)
    assert COMMAND_TEMPLATES["build"] in catalog.render_system_prompt()


def test_field_types_and_timed_wait_nesting_are_explicit_without_concrete_choices():
    prompt = catalog.render_system_prompt()
    assert "quoted JSON strings zone_<index>/group_<index>" in prompt
    assert "never numeric indices" in prompt
    assert "wait has no seconds or interval field" in prompt
    assert "Do not put seconds directly on wait" in prompt


def test_generic_object_templates_parse_after_typed_placeholder_substitution():
    replacements = {
        "<building_or_addon_name>": "supply_depot", "<unit_name>": "marine",
        "<research_name>": "stimpack", "<build_or_train_or_research>": "train",
        "<target_name>": "marine", "<townhall_id>": "cc_0", "<morph_name>": "orbital_command",
        "<zone_id>": "zone_2", "<another_zone_id>": "zone_4",
        "<attack_or_defend>": "attack", "<outbound_group_id>": "group_2",
        "<positive_integer>": "7", "<positive_number>": "13.5",
    }
    templates = [line.strip().rstrip(",") for line in FORMAT_TEMPLATES.splitlines()
                 if line.strip().startswith('{"action":')]
    assert len(templates) == 12
    assert "never submit placeholders" in FORMAT_TEMPLATES
    assert "WITHOUT quotes" in FORMAT_TEMPLATES
    for template in templates:
        for placeholder, value in replacements.items():
            template = template.replace(placeholder, value)
        entry = json.loads(template)
        batch = [entry] if entry["action"] == "wait" else [entry, {
            "action": "wait", "any_of": [{"condition": "interval", "seconds": 13.5}],
        }]
        if entry.get("all_of") == []:
            del entry["all_of"]  # Canonical serialization omits empty condition lists.
        assert parse_decision(batch).to_dicts() == batch


def test_noop_remains_a_legal_complete_reply_and_empty_wait_defaults_are_truthful():
    batch = parse_decision([{"action": "wait"}])
    assert not batch.actions
    empty = parse_decision([{"action": "wait", "any_of": [], "all_of": []}])
    assert empty.wait == batch.wait
    assert "two empty condition lists" in catalog.render_decision_guide()


def test_all_names_costs_and_documentation_examples_remain_available():
    before = catalog.catalog_as_dicts()
    catalog.render_system_prompt()
    assert catalog.catalog_as_dicts() == before
    schema = catalog.decision_json_schema()
    assert schema["examples"] == list(catalog.decision_examples())
    for example in schema["examples"]:
        assert parse_decision(example).to_dicts() == example
    assert len(catalog.targets_for_action("train")) == 17
    assert len(catalog.targets_for_action("research")) == 31


def test_actual_context_and_export_share_the_same_generic_templates(tmp_path):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset()
        prompt = env.get_system_prompt()
        assert env.get_context()[0]["content"] == prompt
        assert read_episode(env.record_path)["platform_prompt"] == prompt
        assert FORMAT_HEADER.rstrip() in prompt
        for template in COMMAND_TEMPLATES.values():
            assert prompt.count(template) == 1
    finally:
        env.close()
