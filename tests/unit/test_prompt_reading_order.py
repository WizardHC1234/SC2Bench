"""Prompt organization follows the public interaction, not source modules."""

import json

import pytest

from sc2bench_env.catalog import registry as catalog
from sc2bench_env.interface.decision_rules import VerbFieldRule, decision_json_schema
from sc2bench_env.interface.platform_rules import (
    COMBAT_DISPATCH_RULES, COMBAT_RETARGET_RULES, COMBAT_STYLE_RULES, INTERACTION_RULES,
)


@pytest.mark.parametrize("entry", catalog.action_syntax_examples())
def test_minimal_entry_examples_use_exact_schema_fields(entry):
    race = {"chrono_boost": "protoss", "inject_larva": "zerg", "spawn_creep_tumor": "zerg"}.get(entry["name"], "terran")
    schemas = decision_json_schema(race=race)["items"]["oneOf"]
    schema = next(
        row for row in schemas
        if row["properties"]["name"].get("const") == entry["name"]
        and set(row["properties"]["arguments"].get("required", [])) <= set(entry.get("arguments") or {})
        and set(entry.get("arguments") or {}) <= set(row["properties"]["arguments"]["properties"])
    )
    assert schema["additionalProperties"] is False
    if entry not in ({"name": "advance", "arguments": {"seconds": 20}}, {"name": "call_mule", "arguments": {}}):
        assert json.dumps(entry, separators=(",", ":")) not in catalog.render_system_prompt()


def test_decision_process_is_early_and_combat_details_belong_to_the_tool():
    prompt = catalog.render_system_prompt()
    protocol = prompt.split("4. Tool interaction\n", 1)[1].split("\n\n5. Platform execution model\n", 1)[0]
    assert INTERACTION_RULES.strip() in protocol
    assert "staged" in protocol
    assert '[{"action":' not in protocol
    from agents.llm_agent.tools import tool_schemas
    descriptions = {
        item["function"]["name"]: item["function"]["description"]
        for item in tool_schemas()
    }
    assert "Split the requested units" in descriptions["dispatch_group"]
    assert "does not create a group" in descriptions["attack_group"]
    assert "does not create a group" in descriptions["defend_group"]
    for rule in (COMBAT_DISPATCH_RULES, COMBAT_RETARGET_RULES, COMBAT_STYLE_RULES):
        assert rule not in prompt
    assert COMBAT_STYLE_RULES not in catalog.render_action_catalog()


def test_templates_fail_closed_when_action_fields_change(monkeypatch):
    from sc2bench_env.interface import decision_rules
    monkeypatch.setitem(decision_rules.VERB_FIELD_RULES, "train",
                        VerbFieldRule("train", required=("target", "count"), optional=("new_field",)))
    with pytest.raises(RuntimeError, match="allowed action fields"):
        catalog.render_decision_guide()
