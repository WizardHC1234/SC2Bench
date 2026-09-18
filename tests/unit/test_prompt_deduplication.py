"""Compact model references retain all targets, costs and prerequisite chains."""
from sc2bench_env.interface.action_catalog import (
    TERRAN_TARGETS, render_action_catalog, render_system_prompt,
    render_decision_guide, render_observation_guide,
)
from sc2bench_env.interface.platform_rules import (
    ACTION_RULES, ARMY_RULES, CONTROL_RULES, ROLE_RULES, INTERACTION_RULES,
)


def compact_rows():
    """Reconstruct inherited facilities and row metadata from the public text."""
    rows = {}
    facility = ""
    columns = []
    for line in render_action_catalog().splitlines():
        if line.startswith("["):
            facility, columns = "", []
        elif line.startswith("facility="):
            facility = line.split("=", 1)[1]
        elif line.startswith("name |"):
            columns = line.split(" | ")
        elif columns and " | " in line:
            cells = line.split(" | ")
            assert len(cells) == len(columns)
            row = dict(zip(columns, cells))
            name = row["name"]
            assert name not in rows
            row["facility"] = facility
            rows[name] = row
    return rows


def test_compact_reference_preserves_every_cost_facility_and_prerequisite():
    prompt = render_system_prompt()
    assert len(prompt) < 24500  # Includes generic syntax and explicit persistent-work reminders; previously 33,911.
    assert len(render_action_catalog()) < 5200  # Previous catalog 15,719.
    rows = compact_rows()
    assert set(rows) == {spec.name for spec in TERRAN_TARGETS if spec.action != "combat"}
    for spec in TERRAN_TARGETS:
        if spec.action == "combat":
            assert f"- {spec.name}:" in prompt
            continue
        row = rows[spec.name]
        if spec.kind in {"building", "addon", "unit", "research", "morph"}:
            assert row["cost minerals/vespene"] == f"{spec.minerals}/{spec.vespene}"
            time_column = {"build": "build time (s)", "train": "train time (s)",
                           "research": "research time (s)", "upgrade": "morph time (s)"}[spec.action]
            assert float(row[time_column]) == spec.base_time_seconds
            assert row["facility"] == (spec.produced_at if spec.action in {"train", "research"}
                                       else spec.morph_from if spec.action == "upgrade" else "")
            prerequisites = row["additional prerequisites"]
            listed = set() if prerequisites == "none" else set(prerequisites.split(", "))
            inherited = {row["facility"]} if row["facility"] in spec.prerequisites else set()
            assert listed | inherited == set(spec.prerequisites)
            if spec.action == "train":
                assert int(row["supply"]) == spec.supply
            else:
                assert "supply" not in row  # All non-unit supply costs are zero.
                assert spec.supply == 0
        else:
            assert row["prerequisites"] == (", ".join(spec.prerequisites) or "none")
            assert "base_seconds" not in row  # Do not advertise Fake cast/travel durations.
            assert not any("time (s)" in column for column in row)
            if spec.energy:
                assert int(row["energy"]) == spec.energy


def test_detailed_rules_have_one_owner_not_copies_in_every_section():
    catalog, observation = render_action_catalog(), render_observation_guide()
    decision = render_decision_guide()
    for verb, explanation in ACTION_RULES.items():
        assert decision.count(explanation) == 1
        assert explanation not in catalog
        assert explanation not in observation
    assert "soft-reserve" in INTERACTION_RULES
    assert "soft-reserve" not in decision
    assert "visible_enemy_nearby counts" in observation
    assert "visible_enemy_nearby counts" not in CONTROL_RULES


def test_completion_boundaries_and_return_to_home_are_preserved():
    decision = render_decision_guide().lower()
    for phrase in [
        "unfinished entity appears, not when construction finishes",
        "finished training and appeared in the game",
        "research enters the game queue",
        "after issuing the morph command, not after the morph finishes",
        "arrival completes; death fails without replacement",
        "requested/available/missing", "without rebinding",
        "survivors merge into group_0", "no game cancellation or refund",
        "combat_ended reports withdrawal/force destruction",
    ]:
        assert phrase in decision


def test_no_added_strategy_or_new_observation_fields():
    prompt = render_system_prompt()
    assert "does not automatically become a whole-map search" in ARMY_RULES
    assert "AutoDepot is disabled" in CONTROL_RULES
    assert "train reinforcements or replace casualties" in CONTROL_RULES
    assert "Tie with end_reason=time_limit" in ROLE_RULES
    for forbidden in ["last_zone_seen_seconds", "last_scouted_at", "search_and_destroy_recommended=yes",
                      "required_action=Order every", "Strategy.md", "Commander file"]:
        assert forbidden not in prompt


def test_engineering_explanations_and_duplicate_final_check_are_not_in_prompt():
    prompt = render_system_prompt()
    assert "Before replying:" not in prompt
    assert "nearest expansion centers along path waypoints" not in prompt
    assert "structured API/JSON records retain precision" not in prompt
    headings = ["1. Role and objective", "2. Interaction protocol", "3. Reading Observation",
                "4. Actions", "5. Game basics"]
    positions = [prompt.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert prompt.index("Reading Observation and Feedback:") < prompt.index("Action reference:")
    assert prompt.index("Action reference:") < prompt.index("Target tables (terran):") < prompt.index("Game basics:")
    assert "Action Catalog" not in prompt


def test_shared_prerequisites_really_are_inherited_not_repeated_per_row():
    rows = compact_rows()
    assert rows["marine"]["facility"] == "barracks"
    assert rows["marine"]["additional prerequisites"] == "none"
    assert rows["marauder"]["additional prerequisites"] == "barracks_techlab"
    assert rows["infantry_weapons_3"]["facility"] == "engineering_bay"
    assert set(rows["infantry_weapons_3"]["additional prerequisites"].split(", ")) == {
        "infantry_weapons_2", "armory",
    }
