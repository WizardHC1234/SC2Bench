"""One action-local reference, with complete metadata and unchanged exports."""
import copy

import pytest

from sc2bench_env import Environment
from sc2bench_env.interface.action_catalog import (
    catalog_as_dicts, decision_json_schema, render_action_catalog,
    render_system_prompt, targets_for_action,
)
from sc2bench_env.interface.platform_rules import ACTION_RULES, GAME_RULES
from sc2bench_env.interface.prompt_fields import COMMAND_TEMPLATES
from sc2bench_env.recording.reader import read_episode


def chapters():
    prompt = render_system_prompt()
    actions, basics = prompt.split("4. Actions\n", 1)[1].split("\n\n5. Game basics\n", 1)
    return prompt, actions, basics


@pytest.mark.parametrize("verb", ["build", "train", "research", "upgrade", "scout", "scan", "call_mule"])
def test_each_complete_target_table_is_once_immediately_after_its_action(verb):
    prompt, actions, basics = chapters()
    standalone = render_action_catalog()
    block = standalone.split(f"[{verb}]\n", 1)[1].split("\n\n", 1)[0]
    assert actions.count(block) == 1
    assert block not in basics
    assert actions.index(COMMAND_TEMPLATES[verb]) < actions.index(ACTION_RULES[verb]) < actions.index(block)
    assert actions[actions.index(ACTION_RULES[verb]) + len(ACTION_RULES[verb]):].startswith("\n" + block)

    # Reconstruct the embedded rows independently of the production renderer.
    rows, columns, facility = {}, [], ""
    for line in block.splitlines():
        if line.startswith("name |"):
            columns = line.split(" | ")
        elif line.startswith("facility="):
            facility = line.split("=", 1)[1]
        else:
            cells = line.split(" | ")
            assert len(cells) == len(columns)
            row = dict(zip(columns, cells), facility=facility)
            assert row["name"] not in rows
            assert prompt.splitlines().count(line) == 1
            rows[row["name"]] = row
    specs = targets_for_action(verb)
    assert set(rows) == {spec.name for spec in specs}
    for spec in specs:
        row = rows[spec.name]
        expected_facility = (spec.produced_at if verb in {"train", "research"}
                             else spec.morph_from if verb == "upgrade" else "")
        assert row["facility"] == expected_facility
        if verb in {"build", "train", "research", "upgrade"}:
            assert row["cost minerals/vespene"] == f"{spec.minerals}/{spec.vespene}"
            time_column = {"build": "build time (s)", "train": "train time (s)",
                           "research": "research time (s)", "upgrade": "morph time (s)"}[verb]
            assert float(row[time_column]) == spec.base_time_seconds
            additional = row["additional prerequisites"]
            inherited = {expected_facility} if expected_facility in spec.prerequisites else set()
            listed = set() if additional == "none" else set(additional.split(", "))
            assert inherited | listed == set(spec.prerequisites)
            if verb == "train":
                assert int(row["supply"]) == spec.supply
        else:
            assert row["prerequisites"] == (", ".join(spec.prerequisites) or "none")
            assert "base_seconds" not in row
            assert not any("time (s)" in column for column in row)
            if spec.energy:
                assert int(row["energy"]) == spec.energy


def test_last_chapter_is_only_game_basics_not_another_catalog():
    prompt, actions, basics = chapters()
    assert basics == GAME_RULES.rstrip() + "\n"
    assert "Action Catalog" not in prompt
    assert "Target tables (terran):" in actions
    assert "cost minerals/vespene" not in basics
    assert prompt.count("Costs: minerals/vespene per item") == 1
    assert prompt.count("Times: approximate game seconds per item") == 1


def test_target_notes_follow_the_relevant_table_not_the_last_chapter():
    _, actions, basics = chapters()
    build, rest = actions.split("\nbuild\n", 1)[1].split("\ntrain\n", 1)
    train = rest.split("\nresearch\n", 1)[0]
    assert "supply_depot provides +8 supply when ready" in build
    assert "hellbat trains directly with Armory" in train
    assert "Viking forms share one identity" in train
    assert "Target notes:" not in basics


def test_rendering_does_not_change_metadata_schema_or_action_semantics():
    before = copy.deepcopy((catalog_as_dicts(), decision_json_schema(), ACTION_RULES, COMMAND_TEMPLATES))
    render_system_prompt()
    render_action_catalog()
    assert (catalog_as_dicts(), decision_json_schema(), ACTION_RULES, COMMAND_TEMPLATES) == before


def test_environment_context_and_prompt_export_have_the_same_local_tables(tmp_path):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset()
        prompt = render_system_prompt()
        assert env.get_system_prompt() == prompt
        assert env.get_context()[0]["content"] == prompt
        assert read_episode(env.record_path)["platform_prompt"] == prompt
        assert "Action Catalog" not in prompt
    finally:
        env.close()
