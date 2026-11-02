"""Filtered queries must retain honest totals, locations and error scope."""
import pytest

from sc2bench_env.runtime.query_tools import query_army_state, query_production_state
from sc2bench_env.adapters.llm.tool_results import render_tool_result


def test_filtered_army_query_keeps_total_outbound_group_count():
    result = query_army_state({"combat": {
        "group_0": {"alive": {}}, "group_1": {"alive": {"marine": 2}},
        "group_2": {"alive": {"marine": 3}},
    }}, groups=["group_0"])
    text = render_tool_result("query_army_state", result)
    assert "Outbound groups: 2" in text
    assert set(result["groups"]) == {"group_0"}


def test_reserved_morph_sources_are_not_labeled_outbound():
    result = query_army_state({"own_forces": {
        "army": {"high_templar": 2}, "assigned": {"high_templar": 2}, "free": {},
    }, "combat": {"group_0": {"alive": {}}}})
    text = render_tool_result("query_army_state", result)
    assert "Unavailable for dispatch: high_templar x2" in text
    assert "Assigned outbound:" not in text


def test_destination_is_not_rendered_as_observed_group_location():
    result = query_army_state({"combat": {"group_1": {
        "style": "attack", "target": "zone_9", "status": "active", "alive": {"marine": 4},
        "phase": "moving",
    }}})
    text = render_tool_result("query_army_state", result)
    assert "Location: zone_9" not in text
    assert "Current order: attack zone_9" in text
    assert "Phase: moving" in text
    assert "Nearest zone: unknown" in text
    from sc2bench_env.interface.observations import render_observation_text
    full = render_observation_text({"combat": result["groups"]})
    assert "Location: zone_9" not in full and "Nearest zone: unknown" in full


def test_invalid_production_target_is_not_silently_reported_as_empty():
    result = query_production_state({"building": {}, "production_priority": []}, targets=["siege_tnak"])
    text = render_tool_result("query_production_state", result)
    assert result["unknown_targets"] == ["siege_tnak"]
    assert "Unknown production targets: siege_tnak" in text


def test_valid_absent_target_and_active_townhall_id_are_not_unknown():
    result = query_production_state({"production_priority": [
        {"action": "morph_townhall", "target": "townhall_0", "to": "orbital_command", "remaining": 1},
    ]}, targets=["battlecruiser", "townhall_0"])
    assert not result.get("unknown_targets")


def test_townhall_id_filter_keeps_source_and_observed_morph():
    result = query_production_state({
        "structures": [{"id": "townhall_0", "type": "hatchery"}],
        "building": {"hatchery": {"completed": 1}, "lair": {"under_construction": 1}},
        "production_priority": [
            {"action": "morph_townhall", "target": "townhall_0", "to": "lair", "remaining": 1},
        ],
    }, targets=["townhall_0"], race="zerg")
    assert set(result["infrastructure"]) == {"hatchery", "lair"}
    assert result["observed_work"]["construction"] == {"lair": 1}


@pytest.mark.parametrize("inventory,shown", [(None, "unknown"), ({}, "none")])
def test_read_query_text_keeps_unknown_inventories_distinct_from_empty(inventory, shown):
    text = render_tool_result("query_zone_state", {"zones": [{
        "zone_id": "zone_0",
        "own_contents": {"units": inventory, "buildings": inventory},
        "visible_enemy_contents": {"units": inventory, "buildings": inventory},
        "last_seen_enemy_contents": {"units": inventory, "buildings": inventory},
    }]})
    assert f"Own: units={shown}; buildings={shown}" in text
    assert f"Visible enemy: units={shown}; buildings={shown}" in text
    text = render_tool_result("query_army_state", {"own_forces": {
        "army": inventory, "free": inventory, "assigned": inventory,
    }, "groups": {}})
    assert f"Total army: {shown}" in text
    assert f"Available from group_0: {shown}" in text


@pytest.mark.parametrize("groups,shown,count", [(None, "unknown", None), ({}, "none", 0)])
def test_unobserved_groups_are_not_reported_as_zero_outbound_groups(groups, shown, count):
    result = query_army_state({"combat": groups}, groups=["group_1"])
    assert result["outbound_group_count"] == count
    assert result["unknown_groups"] == ([] if groups is None else ["group_1"])
    text = render_tool_result("query_army_state", result)
    assert f"Groups:\n- {shown}" in text
