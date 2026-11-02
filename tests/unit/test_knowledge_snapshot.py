"""Snapshot-backed knowledge: client numbers, static tech paths, fail closed."""

import json
from pathlib import Path

import pytest

from sc2bench_env.catalog.knowledge import record_fields, require_snapshot_match
from sc2bench_env.catalog.registry import get_catalog, get_target
from sc2bench_env.adapters.llm.tool_results import render_tool_result
from sc2bench_env.runtime.query_tools import (
    query_building_data, query_prerequisite_path, query_race_data,
    query_research_data, query_unit_data,
)


def test_linux_profile_uses_its_extracted_snapshot():
    profile = json.loads(Path("data/sc2/profiles/linux_official.json").read_text(encoding="utf-8"))
    assert profile["status"] == "extracted"
    assert profile["snapshot_dir"] == "4.10.0.75689_B89B5D6FA7CBF6452E721311BFBC6CB2"
    assert profile["expected_base_build"] == 75689


def test_live_snapshot_stays_on_the_calling_context():
    from sc2bench_env.catalog.knowledge import bound_snapshot, game_data

    windows = bound_snapshot()
    linux = require_snapshot_match(
        game_version="4.10.0.75689",
        data_version="B89B5D6FA7CBF6452E721311BFBC6CB2",
        base_build=75689,
    )
    assert linux["manifest"]["game_version"] == "4.10.0.75689"
    with game_data(linux):
        assert bound_snapshot()["manifest"]["game_version"] == "4.10.0.75689"
        assert bound_snapshot()["profile_id"] == "linux-official-4.10.0"
    assert bound_snapshot()["manifest"]["game_version"] == windows["manifest"]["game_version"]


def test_bound_snapshot_is_recorded_and_a_mismatch_is_refused():
    fields = record_fields()
    assert fields["knowledge_game_version"] == "5.0.16.97563"
    assert fields["knowledge_data_version"]
    assert fields["knowledge_snapshot_hash"]
    with pytest.raises(RuntimeError, match="no knowledge snapshot"):
        require_snapshot_match(game_version="4.10.0.0", data_version="missing", base_build=1)


def test_unit_query_includes_client_combat_and_unknown_proto_gaps():
    marine = query_unit_data(["marine"], race="terran")["results"][0]
    assert marine["health"] == 45.0
    assert marine["shields"] == "none"
    assert marine["energy_max"] == "none"
    assert marine["can_attack"] == "ground_and_air"
    assert marine["forms"][0]["movement_layer"] == "ground"
    assert "damage" in marine["forms"][0]["weapons"][0]
    text = render_tool_result("query_unit_data", {"results": [marine]})
    assert "Health: 45" in text
    assert "Shields: none" in text
    assert "Energy max: none" in text
    assert "Data gaps:" not in text
    assert "name unknown" not in text
    assert "base_dps" in text
    zergling = query_unit_data(["zergling"], race="zerg")["results"][0]
    assert zergling["supply"] == 1
    assert zergling["client_food_required"] == 0.5


def test_text_results_expose_supply_and_keep_research_fields_semantic():
    depot = query_building_data(["supply_depot"], race="terran")["results"][0]
    assert depot["food_provided"] == 8
    depot_text = render_tool_result("query_building_data", {"results": [depot]})
    assert "Supply provided when complete: 8" in depot_text

    stimpack = query_research_data(["stimpack"], race="terran")["results"][0]
    research_text = render_tool_result("query_research_data", {"results": [stimpack]})
    assert "Effect:" in research_text
    assert "Health:" not in research_text
    assert "Can attack:" not in research_text


@pytest.mark.parametrize("race,target", [
    ("terran", "orbital_command"), ("terran", "planetary_fortress"),
    ("protoss", "warpgate"), ("zerg", "lair"), ("zerg", "hive"),
])
def test_building_morph_supply_is_labeled_as_the_completed_form(race, target):
    row = query_building_data([target], race=race)["results"][0]
    text = render_tool_result("query_building_data", {"results": [row]})
    assert "Supply provided by completed form:" in text
    assert "Supply provided when complete:" not in text
    assert f"Morphs from: {row.get('morph_from') or row['builder']}" in text


def test_runtime_vitals_come_from_the_same_client_version():
    stalker = query_unit_data(["stalker"], race="protoss")["results"][0]
    assert stalker["health"] == 80.0
    assert stalker["shields"] == 80.0
    assert stalker["energy_max"] == "none"
    queen = query_unit_data(["queen"], race="zerg")["results"][0]
    assert queen["health"] == 175.0
    assert queen["energy_max"] == 200.0
    assert queen["shields"] == "none"
    medivac = query_unit_data(["medivac"], race="terran")["results"][0]
    assert medivac["health"] == 150.0
    assert medivac["energy_max"] == 200.0
    turret = query_building_data(["missile_turret"], race="terran")["results"][0]
    assert turret["health"] == 250.0
    assert turret["shields"] == "none"
    center = query_building_data(["command_center"], race="terran")["results"][0]
    assert center["health"] == 1500.0
    assert center["food_provided"] == 13
    nexus = query_building_data(["nexus"], race="protoss")["results"][0]
    assert nexus["food_provided"] == 13
    tumor = query_building_data(["creep_tumor"], race="zerg")["results"][0]
    assert tumor["health"] == "unknown"
    reactor = query_building_data(["starport_reactor"], race="terran")["results"][0]
    assert reactor["health"] == "unknown"


def test_roach_text_distinguishes_platform_control_from_snapshot_gaps():
    roach = query_unit_data(["roach"], race="zerg")["results"][0]
    assert roach["roles"] == ["combat"]
    burrowed = next(form for form in roach["forms"] if form["proto_name"] == "RoachBurrowed")
    assert burrowed["movement_layer"] == "ground"
    assert "backend may burrow" in roach["form_note"]
    text = render_tool_result("query_unit_data", {"results": [roach]})
    assert "Supply provided when complete: none" in text
    assert text.count("backend may burrow") == 1


def test_observable_names_stay_inside_their_race():
    terran = query_race_data("terran")
    protoss = query_race_data("protoss")
    assert "mule" in terran["observable_only"]["units"]
    assert "archon" not in terran["observable_only"]["units"]
    assert "archon" not in protoss["observable_only"]["units"]
    assert "archon" in protoss["units"]
    wrong = query_unit_data(["mule"], race="zerg")["results"][0]
    assert wrong["error"] == "unknown_target"


def test_observable_alias_is_queryable_and_unknown_names_suggest_candidates():
    mule = query_unit_data(["mule"], race="terran")["results"][0]
    assert mule["availability"] == "observable_only"
    assert mule["name"] == "mule"
    missing = query_unit_data(["marne"], race="terran")["results"][0]
    assert missing["error"] == "unknown_target"
    assert "marine" in missing["candidates"]
    race = query_race_data("terran")
    assert "mule" in race["observable_only"]["units"]
    assert "marine" in race["units"]


def test_prerequisite_path_is_a_static_table_for_every_controllable_target():
    for race in ("terran", "protoss", "zerg"):
        for spec in get_catalog(race=race).targets:
            if spec.action not in {"train", "build", "research", "morph_townhall"}:
                continue
            result = query_prerequisite_path(spec.name, race=race)
            assert result["static_dependency_path"] is True
            assert result["steps"][-1]["target"] == spec.name
            assert "minerals" in result["steps"][-1]
    tank = query_prerequisite_path("siege_tank", race="terran")
    text = render_tool_result("query_prerequisite_path", tank)
    assert "Static dependency path, not a match build order." in text
    assert "Step | Type | Target | Minerals | Gas | Time | Requires" in text
    typo = query_prerequisite_path("stimpak", race="terran")
    assert "stimpack" in typo["candidates"]
    assert "Close candidates: stimpack" in render_tool_result("query_prerequisite_path", typo)


def test_race_catalog_text_includes_platform_abilities():
    result = query_race_data("terran")
    text = render_tool_result("query_race_data", result)
    assert "Platform abilities:" in text
    assert "scan" in text and "call_mule" in text


def test_client_button_costs_override_stale_catalog_literals():
    assert get_target("queen", race="zerg").minerals == 175
    assert get_target("ultralisk", race="zerg").minerals == 275
    assert get_target("greater_spire", race="zerg").minerals == 100
    assert get_target("greater_spire", race="zerg").vespene == 150
    assert get_target("spire", race="zerg").minerals == 150
    assert get_target("spire", race="zerg").vespene == 150
    assert get_target("barracks_techlab").minerals == 50
    assert get_target("barracks_techlab").vespene == 25
