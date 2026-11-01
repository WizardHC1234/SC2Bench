"""Text compaction changes presentation, never planning rights or world facts."""
import copy

import pytest

from sc2bench_env.catalog.registry import (
    render_decision_guide, render_observation_guide, render_system_prompt,
)
from sc2bench_env.interface.observation_text import production_lines, _production_unit_options
from sc2bench_env.interface.observations import render_observation_text
from sc2bench_env.interface.platform_rules import ACTION_RULES, DECISION_REQUEST
from sc2bench_env.interface.prompt_fields import COMMAND_TEMPLATES


def test_schema_forms_are_once_and_next_to_their_completion_meaning():
    prompt = render_system_prompt()
    from agents.llm_agent.tools import tool_schemas
    descriptions = {
        race: {
            row["function"]["name"]: row["function"]["description"]
            for row in tool_schemas(race=race)
        }
        for race in ("terran", "protoss", "zerg")
    }
    race_owner = {
        "chrono_boost": "protoss",
        "inject_larva": "zerg",
        "spawn_creep_tumor": "zerg",
    }
    for verb, template in COMMAND_TEMPLATES.items():
        assert template not in prompt
        name = verb
        owner = race_owner.get(name, "terran")
        assert name in descriptions[owner]
        assert descriptions[owner][name]
        # Shared ACTION_RULES remain the source for common verbs; race-local
        # morph/build wording may specialize the schema description.
        if name not in {"morph_townhall", "build", "train", "cancel"}:
            assert ACTION_RULES[name] == descriptions[owner][name]
    assert "supply_left_at_least" not in prompt


def test_fixed_explanations_not_repeated_in_dynamic_observation():
    observation = {"production_priority": [{"action": "train", "target": "marine", "remaining": 3,
                                           "order_progress": "2/5", "in_production": 1,
                                           "waiting_to_produce": 2, "state": "waiting",
                                           "waiting_for": "resources"}],
                   "production": [{"facility": "factory", "ready_grounded": 1, "techlab_hosts": 0}],
                   "building": {}, "training": {}}
    before = copy.deepcopy(observation)
    text = render_observation_text(observation)
    assert "1. train marine; remaining 3; paid 1; unstarted 2" in text
    assert "resource budget unavailable" in text
    for explanation in ("Repeating build/train adds EXTRA work", "does not mean affordable",
                        "originally requested, not living inventory", "Use explicit train requests",
                        "ALREADY ACCEPTED WORK"):
        assert explanation not in text
    assert observation == before
    assert len(DECISION_REQUEST) < 450
    assert "no prescribed composition" not in render_system_prompt()


@pytest.mark.parametrize("labs,buildings", [
    (0, {}), (1, {"armory": {"completed": 1}}),
    (None, None), (1, {"armory": {"completed": None}}),
])
def test_technology_helper_preserves_facts_but_briefing_does_not_repeat_them(labs, buildings):
    row = {"facility": "factory", "ready_grounded": 1, "techlab_hosts": labs}
    before = copy.deepcopy((row, buildings))
    old_options = _production_unit_options(row, buildings)
    text = "\n".join(production_lines([row], buildings, {}))
    assert len(old_options) == 6
    assert {item.split(" ", 1)[0] for item in old_options} == {
        "cyclone", "hellbat", "hellion", "siege_tank", "thor", "widow_mine",
    }
    assert "Unit technology:" not in text
    assert (row, buildings) == before
    guide = render_observation_guide()
    assert "factory" not in guide and "armory" not in guide


def test_template_drift_fails_closed_not_silently_hiding_a_combat_form(monkeypatch):
    monkeypatch.setitem(COMMAND_TEMPLATES, "dispatch_group", COMMAND_TEMPLATES["attack_group"])
    with pytest.raises(RuntimeError, match="action form"):
        render_decision_guide()
