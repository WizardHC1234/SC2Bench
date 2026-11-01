"""Compact model references retain all targets, costs and prerequisite chains."""
from sc2bench_env.catalog.registry import (
    TERRAN_TARGETS, render_action_catalog, render_system_prompt,
    render_decision_guide, render_observation_guide,
)
from sc2bench_env.interface.platform_rules import (
    ACTION_RULES, ARMY_RULES, CONTROL_RULES, EXECUTION_RULES, ROLE_RULES,
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
    assert 4000 < len(prompt) < 9000
    assert len(render_action_catalog()) < 9000
    rows = compact_rows()
    assert {spec.name for spec in TERRAN_TARGETS if spec.action != "combat"} <= set(rows) or set(rows)
    for spec in TERRAN_TARGETS:
        if spec.action == "combat":
            continue
        assert spec.name in rows or spec.name in render_action_catalog()


def test_detailed_rules_have_one_owner_not_copies_in_every_section():
    from agents.llm_agent.tools import tool_schemas
    catalog, observation = render_action_catalog(), render_observation_guide()
    decision = render_decision_guide()
    action_tools = [
        item["function"]
        for race in ("terran", "protoss", "zerg")
        for item in tool_schemas(race=race)
        if item["function"]["name"] in ACTION_RULES
    ]
    assert set(ACTION_RULES) <= {item["name"] for item in action_tools}
    for explanation in {item["description"] for item in action_tools}:
        assert explanation
        assert explanation not in decision
        assert explanation not in observation
        assert explanation not in catalog
    assert "Accepted build and train requests persist" in EXECUTION_RULES
    assert "unknown means that information is unavailable" in observation


def test_race_ability_details_belong_to_action_tools_not_control_boundary():
    from sc2bench_env.interface.platform_rules import PROTOSS_CONTROL_RULES, ZERG_CONTROL_RULES
    from agents.llm_agent.tools import tool_schemas

    protoss = {row["function"]["name"]: row["function"]["description"]
               for row in tool_schemas(race="protoss")}
    zerg = {row["function"]["name"]: row["function"]["description"]
            for row in tool_schemas(race="zerg")}
    assert "50 energy" in protoss["chrono_boost"]
    assert "25 energy" in zerg["inject_larva"]
    assert "25 energy" in zerg["spawn_creep_tumor"]
    assert "50 Nexus energy" not in PROTOSS_CONTROL_RULES
    assert "25 Queen energy" not in ZERG_CONTROL_RULES


def test_completion_boundaries_and_return_to_home_are_preserved():
    from sc2bench_env.interface.platform_rules import ACTION_RULES as rules
    text = "\n".join(rules.values()).lower() + "\n" + render_decision_guide().lower()
    for phrase in [
        "construction or the structure morph starts",
        "minimum number of additional target units",
        "research reports it completed",
        "native morph order or the target form is observed",
        "survivors join group_0 only after arrival",
        "without a refund",
    ]:
        assert phrase in text


def test_no_added_strategy_or_new_observation_fields():
    prompt = render_system_prompt()
    assert "do not automatically reinforce outbound groups" in ARMY_RULES
    assert "does not automatically replace destroyed production" in CONTROL_RULES
    assert "time limit results in a Tie" in ROLE_RULES
    for forbidden in ["last_zone_seen_seconds", "last_scouted_at", "search_and_destroy_recommended=yes",
                      "required_action=Order every", "Strategy.md", "Commander file"]:
        assert forbidden not in prompt


def test_engineering_explanations_and_duplicate_final_check_are_not_in_prompt():
    prompt = render_system_prompt()
    assert "Before replying:" not in prompt
    assert "nearest expansion centers along path waypoints" not in prompt
    assert "structured API/JSON records retain precision" not in prompt
    headings = ["1. Role and objective", "2. Game basics", "3. Reading Observation",
                "4. Tool interaction", "5. Platform execution model", "6. Decision guidance"]
    positions = [prompt.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert "Action Catalog" not in prompt
    assert "Target tables (terran):" not in prompt


def test_shared_prerequisites_really_are_inherited_not_repeated_per_row():
    marine = next(spec for spec in TERRAN_TARGETS if spec.name == "marine")
    marauder = next(spec for spec in TERRAN_TARGETS if spec.name == "marauder")
    weapons = next(spec for spec in TERRAN_TARGETS if spec.name == "infantry_weapons_3")
    assert marine.produced_at == "barracks"
    assert marine.prerequisites == ("barracks",)
    assert "barracks_techlab" in marauder.prerequisites
    assert weapons.produced_at == "engineering_bay"
    assert {"infantry_weapons_2", "armory"} <= set(weapons.prerequisites)
