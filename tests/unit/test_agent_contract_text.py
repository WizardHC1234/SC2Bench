"""Platform text must describe the same contract as the parser and state API."""
import json

import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.action_catalog import (
    action_syntax_examples, decision_examples, decision_json_schema,
    render_decision_guide, render_system_prompt, wait_condition_examples,
)
from sc2bench_env.interface.actions import parse_decision
from sc2bench_env.interface.decision_rules import VERB_FIELD_RULES, WAIT_CONDITION_RULES
from sc2bench_env.interface.observations import OBSERVATION_SECTIONS
from sc2bench_env.recording.context import observation_text


@pytest.mark.parametrize("example", decision_examples())
def test_prompt_examples_parse_and_match_schema_field_contract(example):
    batch = parse_decision(example)
    assert batch.to_dicts() == example
    schemas = decision_json_schema()["items"]["oneOf"]
    for entry in example:
        schema = next(item for item in schemas
                      if item["properties"]["action"].get("const") == entry["action"]
                      and set(item["required"]) <= set(entry) <= set(item["properties"]))
        assert set(schema["required"]) <= set(entry) <= set(schema["properties"])
        assert schema["additionalProperties"] is False
    # Schema retains examples; concrete decisions are not injected into the prompt.
    for entry in example[:-1]:
        if entry == {"action": "call_mule"}:  # No parameters or tactical choices to instantiate.
            continue
        assert json.dumps(entry, separators=(",", ":")) not in render_system_prompt()


def test_examples_cover_every_current_action_without_internal_ids():
    examples = decision_examples()
    assert {row["action"] for example in examples for row in example} == set(VERB_FIELD_RULES)
    assert "action_id" not in json.dumps(examples)
    assert decision_json_schema()["examples"] == list(examples)


def test_text_fields_share_the_actual_parser_rules():
    from sc2bench_env.interface.prompt_fields import ACTION_FIELDS, WAIT_FIELDS, COMMAND_TEMPLATES
    guide = render_decision_guide()
    fields = {}
    for entry in action_syntax_examples():
        rule = VERB_FIELD_RULES[entry["action"]]
        assert set(rule.required) <= set(entry) <= rule.allowed
        fields.setdefault(entry["action"], set()).update(entry)
        parse_decision([entry] if entry["action"] == "wait" else [entry, {"action": "wait"}])
    assert fields == {verb: set(rule.allowed) for verb, rule in VERB_FIELD_RULES.items()}
    assert {verb: set(descriptions) | {"action"} for verb, descriptions in ACTION_FIELDS.items()} == fields
    # Syntax is now shown once beside its semantics, not as a second field list.
    import re
    shown = {"wait": {"action", "any_of", "all_of"}}
    for template in COMMAND_TEMPLATES.values():
        assert guide.count(template) == 1
        entry = json.loads(re.sub(r"(?<!\")<positive_integer>", "1", template))
        shown.setdefault(entry["action"], set()).update(entry)
    assert shown == fields
    for entry in wait_condition_examples():
        assert json.dumps(entry, separators=(",", ":")) not in guide
        rule = WAIT_CONDITION_RULES[entry["condition"]]
        assert set(entry) - {"condition"} == rule.allowed_params
        assert set(WAIT_FIELDS[entry["condition"]]) == rule.allowed_params
        for name, description in WAIT_FIELDS[entry["condition"]].items():
            assert f"{name}: {description}" in guide
        parse_decision([{"action": "wait", "any_of": [entry]}])
    assert "required=" not in guide and "optional=" not in guide


def test_observation_context_and_section_lines_use_one_complete_view():
    env = Environment(FakeBackend())
    try:
        obs = env.reset()
        obs.game.seconds_remaining = 12
        obs.economy.mineral_income_per_minute = 1234.5
        obs.combat = {"group_1": {
            "phase": "withdrawing", "requested": {"marine": 8}, "alive": {"marine": 5},
        }}
        obs.scouting = {"scv": {"route": ["zone_1", "zone_2"], "moving_to": "zone_2"}}
        obs.building = {"barracks": {"waiting_to_start": 1, "waiting_for": "prerequisite:supply_depot"}}
        obs.training = {"marine": {"order_progress": "6/8", "waiting_to_produce": 2}}
        text = observation_text(obs.to_dict())
        assert "\n".join(obs.section_lines()) == text
        for _, label in OBSERVATION_SECTIONS:
            assert f"[{label}]" in text
        assert "Time remaining: 00:12 (12 s)" in text
        assert "Income per minute: minerals 1234.5" in text
        assert "phase withdrawing" in text
        assert "Living members: marine 5" in text
        assert "Moving to: zone_2" in text
        assert "requires ready supply_depot" in text
        assert "marine | unknown | 2 | unknown" in text
        assert "6/8" not in text
        assert "[Previous Feedback]" not in text
    finally:
        env.close()


def test_context_keeps_previous_feedback_separate_from_current_observation():
    env = Environment(FakeBackend())
    try:
        env.reset()
        env.step([{"action": "wait"}])
        messages = env.get_context()
        assert messages[0]["content"] == env.get_system_prompt()
        assert "[Current Observation]" in messages[1]["content"]
        assert "[Previous Feedback]" in messages[1]["content"]
    finally:
        env.close()


def test_research_mapping_only_includes_truthy_in_progress_values():
    env = Environment(FakeBackend())
    try:
        env.reset()
        snapshot = env.backend.snapshot()
        snapshot.info["upgrades"] = ["concussive_shells"]
        snapshot.info["in_progress_research"] = {"concussive_shells": False, "stimpack": True}
        obs = env._build_observation(snapshot)
        assert obs.research == {"concussive_shells": "completed", "stimpack": "in_progress"}
    finally:
        env.close()


def test_prompt_explains_progress_repeat_and_control_boundaries():
    prompt = render_system_prompt()
    for phrase in (
        "Sharpy handles placement", "collection-rate score", "Paid training queue",
        "Append one building", "N additional births", "No game cancellation or refund",
        "does not bypass all_of", "not tactical success", "Fog of war is partial information",
        "never waiting for future train orders or partially dispatching", "paid queued units",
        "visible_enemy_weapon_in_range means a currently visible enemy weapon",
        "not current ownership", "never calls fogged territory neutral",
        "at most 60 game seconds", "does not terminate the episode",
    ):
        assert phrase in prompt
