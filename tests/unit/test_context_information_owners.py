"""Text compaction changes presentation, never planning rights or world facts."""
import copy
import re

import pytest

from sc2bench_env.interface.action_catalog import render_system_prompt, render_observation_guide
from sc2bench_env.interface.observation_text import production_lines, _production_unit_options
from sc2bench_env.interface.observations import render_observation_text
from sc2bench_env.interface.platform_rules import ACTION_RULES, DECISION_REQUEST
from sc2bench_env.interface.prompt_fields import COMMAND_TEMPLATES


def test_schema_forms_are_once_and_next_to_their_completion_meaning():
    prompt = render_system_prompt()
    for verb, template in COMMAND_TEMPLATES.items():
        assert prompt.count(template) == 1
        name = "combat" if verb.startswith("combat_") else verb
        if name not in {"combat"}:
            assert prompt.index(template) < prompt.index(ACTION_RULES[name])
    assert "Only the eight condition names below are supported" in prompt
    assert "do not invent inverses or new names" in prompt
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
    assert "1 | train | marine | 3 | 2/5 | 1 | 2 | waiting | resource budget unavailable" in text
    assert "ALREADY ACCEPTED WORK" in text
    for explanation in ("Repeating build/train adds EXTRA work", "does not mean affordable",
                        "originally requested, not living inventory", "Use explicit train requests"):
        assert explanation not in text
    assert observation == before
    assert len(DECISION_REQUEST) < 350
    assert "no prescribed composition" in render_system_prompt()


@pytest.mark.parametrize("labs,buildings", [
    (0, {}), (1, {"armory": {"completed": 1}}),
    (None, None), (1, {"armory": {"completed": None}}),
])
def test_grouped_technology_preserves_every_unit_and_exact_blockers(labs, buildings):
    row = {"facility": "factory", "ready_grounded": 1, "techlab_hosts": labs}
    before = copy.deepcopy((row, buildings))
    old_options = _production_unit_options(row, buildings)
    text = "\n".join(production_lines([row], buildings, {}))
    groups = {}
    for line in text.splitlines():
        if line.startswith("    "):
            # Conditions may themselves contain ':', e.g. unknown readiness.
            condition, names = line.strip().rsplit(": ", 1)
            for name in names.split(", "):
                assert name not in groups
                groups[name] = condition
    expected = dict(re.match(r"^(\w+) \((.*)\)$", item).groups() for item in old_options)
    assert groups == expected
    assert (row, buildings) == before
    assert "does not mean affordable or an available slot" in render_observation_guide()


def test_template_drift_fails_closed_not_silently_hiding_a_combat_form(monkeypatch):
    monkeypatch.setitem(COMMAND_TEMPLATES, "combat_units", COMMAND_TEMPLATES["combat_group"])
    with pytest.raises(RuntimeError, match="action form"):
        render_system_prompt()
