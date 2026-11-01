"""Platform text must describe the same contract as the parser and state API."""
from tests.helpers.drive import drive
from sc2bench_env.adapters.llm import LLMAdapter
from agents.llm_agent.prompts import example_prompt_parts, platform_turn_messages
import json

import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.catalog.registry import (
    action_syntax_examples, decision_examples,
    render_decision_guide, render_system_prompt,
)
from sc2bench_env.interface.actions import parse_decision
from sc2bench_env.interface.decision_rules import decision_json_schema
from sc2bench_env.interface.observations import observation_lines, OBSERVATION_SECTIONS
from sc2bench_env.interface.observations import render_observation_text as observation_text


@pytest.mark.parametrize("example", decision_examples())
def test_prompt_examples_parse_and_match_schema_field_contract(example):
    batch = parse_decision(example)
    assert batch.to_dicts() == example
    schemas = decision_json_schema()["items"]["oneOf"]
    for entry in example:
        schema = next(item for item in schemas
                      if item["properties"]["name"].get("const") == entry["name"]
                      and set(item["properties"]["arguments"].get("required", [])) <= set(entry.get("arguments") or {})
                      and set(entry.get("arguments") or {}) <= set(item["properties"]["arguments"]["properties"]))
        assert set(schema["required"]) == {"name", "arguments"}
        assert schema["additionalProperties"] is False
    # Schema retains examples; concrete decisions are not injected into the prompt.
    for entry in example[:-1]:
        if entry == {"name": "call_mule", "arguments": {}}:  # No parameters or tactical choices to instantiate.
            continue
        assert json.dumps(entry, separators=(",", ":")) not in render_system_prompt()


def _example_race(entry: dict) -> str:
    encoded = json.dumps(entry, sort_keys=True)
    for race in ("terran", "protoss", "zerg"):
        for batch in decision_examples(race=race):
            for row in batch:
                if json.dumps(row, sort_keys=True) == encoded:
                    return race
    return "terran"


def test_examples_cover_every_current_action_without_internal_ids():
    from sc2bench_env.interface.tools import action_tool_names

    covered = set()
    public = set()
    for race in ("terran", "protoss", "zerg"):
        public.update(action_tool_names(race))
        examples = decision_examples(race=race)
        for example in examples:
            assert parse_decision(example, race=race).to_dicts() == example
            covered.update(row["name"] for row in example)
        if race == "terran":
            assert "action_id" not in json.dumps(examples)
            assert decision_json_schema()["examples"] == list(examples)
    assert covered == public


def test_text_fields_share_the_actual_parser_rules():
    from sc2bench_env.interface.prompt_fields import (
        ACTION_FIELDS, COMMAND_TEMPLATES, NUMERIC_PLACEHOLDER_RE,
    )
    from sc2bench_env.interface.tools import tool_specs

    public_specs = {}
    for race in ("terran", "protoss", "zerg"):
        for spec in tool_specs(race):
            if spec.kind == "action":
                public_specs[(race, spec.name)] = spec
    for entry in action_syntax_examples():
        arguments = dict(entry.get("arguments") or {})
        race = _example_race(entry)
        spec = public_specs[(race, entry["name"])]
        assert set(spec.parameters.get("required", ())) <= set(arguments)
        assert set(arguments) <= set(spec.parameters.get("properties", {}))
        batch = [entry] if entry["name"] == "advance" else [entry, {"name": "advance", "arguments": {"seconds": 5}}]
        parse_decision(batch, race=race)
    assert set(ACTION_FIELDS) == {name for _, name in public_specs}
    for name, template in COMMAND_TEMPLATES.items():
        entry = json.loads(NUMERIC_PLACEHOLDER_RE.sub("1", template))
        assert entry["name"] == name
        assert set(entry.get("arguments") or {}) <= set(ACTION_FIELDS[name])
    guide = render_decision_guide()
    assert "required=" not in guide and "optional=" not in guide
    for template in COMMAND_TEMPLATES.values():
        assert template not in guide


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
        assert "\n".join(observation_lines(obs)) == text
        for _, label in OBSERVATION_SECTIONS:
            if label in {"Map Topology", "Relevant Zones"}:
                continue
            assert f"[{label}]" in text
        assert "Time 00:00/10:00 (00:12 remaining)" in text
        assert "Mineral workers: 12 / saturation 16" in text
        assert "Vespene workers: 0 / saturation 0" in text
        assert "Other workers: 0" in text
        assert "mining worker capacity" not in text.lower()
        assert "Income per minute: minerals 1234.5" in text
        assert "group_1: marine 5" in text
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
        drive(env, [{"name": "advance", "arguments": {'seconds': 5}}])
        messages = platform_turn_messages(env.latest_observation, env.latest_feedback, env.config.race if env.config else "terran")
        race = env.config.race if env.config else "terran"
        assert messages[0]["content"] == LLMAdapter(
            example_prompt_parts(race), race=race).system_prompt()
        assert "submit one or more chosen actions without advance" in messages[0]["content"]
        assert "cancel matching unstarted work by target action" in messages[0]["content"]
        assert "describing an action or a future plan does not execute it" in messages[0]["content"]
        assert "Do not mix queries, actions and advance in the same reply" in messages[0]["content"]
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
        "You make the strategic decisions for construction, production, research",
        "The backend handles building placement",
        "Production buildings add capacity; units and research still require explicit",
        "Use Read or Knowledge tools when the current decision requires information",
        "Query and Action calls made before advance use the same frozen decision snapshot",
        "call advance by itself",
        "Accepted build and train requests persist across decisions",
    ):
        assert phrase in prompt
    assert '[{"action":' not in prompt
    from sc2bench_env.interface.platform_rules import ACTION_RULES, COMBAT_DISPATCH_RULES
    assert "legal placement" in ACTION_RULES["build"]
    assert "count is the minimum number of additional target units" in ACTION_RULES["train"]
    assert "atomically split" in COMBAT_DISPATCH_RULES
    assert "only change an existing outbound group's order" in COMBAT_DISPATCH_RULES
