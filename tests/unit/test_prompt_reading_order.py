"""Prompt organization follows the public interaction, not source modules."""

import json

import pytest

from sc2bench_env.interface import action_catalog as catalog
from sc2bench_env.interface.decision_rules import VerbFieldRule, WaitConditionRule
from sc2bench_env.interface.platform_rules import (
    COMBAT_DISPATCH_RULES, COMBAT_RETARGET_RULES, COMBAT_STYLE_RULES, INTERACTION_RULES,
)


@pytest.mark.parametrize("entry", catalog.action_syntax_examples())
def test_minimal_entry_examples_use_exact_schema_fields(entry):
    schemas = catalog.decision_json_schema()["items"]["oneOf"]
    schema = next(row for row in schemas if row["properties"]["action"].get("const") == entry["action"]
                  and set(row["required"]) <= set(entry) <= set(row["properties"]))
    assert schema["additionalProperties"] is False
    # Documentation/Schema examples remain valid, but not in the fixed prompt.
    if entry not in ({"action": "wait"}, {"action": "call_mule"}):
        assert json.dumps(entry, separators=(",", ":")) not in catalog.render_system_prompt()


def test_protocol_is_early_and_army_cases_and_styles_have_separate_owners():
    prompt = catalog.render_system_prompt()
    protocol = prompt.split("2. Interaction protocol\n", 1)[1].split("3. Reading Observation\n", 1)[0]
    assert INTERACTION_RULES in protocol
    assert "Return only a JSON array" in protocol
    assert '[{"action":"wait"}]' in protocol
    actions = prompt.split("4. Actions\n", 1)[1].split("5. Game basics\n", 1)[0]
    labels = ["Production and construction:", "Army control:",
              "Dispatch a new group:", "Retarget an existing group:", "Return a group home:",
              "Combat styles:", "Reconnaissance and abilities:", "Wait:"]
    assert [actions.index(label) for label in labels] == sorted(actions.index(label) for label in labels)
    for rule in (COMBAT_DISPATCH_RULES, COMBAT_RETARGET_RULES, COMBAT_STYLE_RULES):
        assert prompt.count(rule) == 1
    assert COMBAT_STYLE_RULES not in catalog.render_action_catalog()


def test_templates_fail_closed_when_action_fields_change(monkeypatch):
    from sc2bench_env.interface import decision_rules
    monkeypatch.setitem(decision_rules.VERB_FIELD_RULES, "train",
                        VerbFieldRule("train", required=("target", "count"), optional=("new_field",)))
    with pytest.raises(RuntimeError, match="allowed action fields"):
        catalog.render_decision_guide()


def test_templates_fail_closed_when_condition_fields_change(monkeypatch):
    from sc2bench_env.interface import decision_rules
    monkeypatch.setitem(decision_rules.WAIT_CONDITION_RULES, "interval",
                        WaitConditionRule("interval", optional=("seconds", "new_field")))
    with pytest.raises(RuntimeError, match="allowed condition fields"):
        catalog.render_decision_guide()
