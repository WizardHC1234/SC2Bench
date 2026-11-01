"""Plain model input preserves facts, API JSON and recorded transcripts."""
from tests.helpers.drive import drive
from agents.llm_agent.prompts import platform_turn_messages
import copy
import json

import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.observations import render_observation_text
from sc2bench_env.interface.observation_text import render_feedback_text
from sc2bench_env.adapters.llm.observation import platform_messages


@pytest.mark.parametrize(
    ("race", "label"),
    (("terran", "SCVs"), ("protoss", "Probes"), ("zerg", "Drones")),
)
def test_worker_count_names_the_race_specific_worker(race, label):
    text = render_observation_text({
        "game": {"race": race},
        "economy": {
            "worker_count": 12,
            "workers_on_minerals": 9,
            "mineral_worker_saturation": 16,
            "workers_on_vespene": 3,
            "vespene_worker_saturation": 3,
            "workers_other": 0,
        },
    })
    assert f"Workers ({label}): 12" in text
    assert "Mineral workers: 9 / saturation 16" in text
    assert "Vespene workers: 3 / saturation 3" in text
    assert "Other workers: 0" in text


def test_model_input_has_no_serialized_json_and_keeps_unknown_zero_empty_distinct():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        obs = env.reset()
        obs.economy.mineral_income_per_minute = None
        obs.combat["group_1"] = {
            "style": "attack", "target": "zone_15", "status": "active",
            "phase": "executing", "alive": {"marine": 8}, "requested": {"marine": 10},
            "visible_enemy_nearby": {"units": 0, "buildings": 0},
            "weapon_cooldown_active_count": None,
            "transport": {"peak_loaded_units": 4, "drop_unloaded": False},
        }
        original = copy.deepcopy(obs.to_dict())
        user = platform_messages("rules", original, {"receipts": [], "events": []})[1]["content"]
        assert "{" not in user and "}" not in user and '"marine"' not in user
        assert "Income/min: minerals unknown" in user
        assert "Nearby enemies: none" in user
        assert "Members with active weapon cooldown" not in user
        assert "Peak loaded units" not in user and "Drop unloaded" not in user
        assert "Originally requested" not in user
        assert "group_1: attack zone_15; executing" in user
        assert "Living: marine 8" in user
        assert "Action receipts:\nnone" in user
        assert original == obs.to_dict()
        assert json.loads(json.dumps(original)) == original
    finally:
        env.close()


def test_zone_rows_keep_roles_ownership_current_and_historical_enemy_separate():
    zone = {"zone_id": "zone_9", "zone_role": "enemy_main", "known_owner": "unconfirmed",
            "vision_state": "fogged", "own_contents": {"units": {}, "buildings": {}},
            "visible_enemy_contents": {"units": {}, "buildings": {}},
            "last_seen_enemy_contents": {"units": {"marine": 2}, "buildings": {"barracks": 1}},
            "enemy_information_age_seconds": 20.125,
            "visible_enemy_weapon_in_range": False}
    text = render_observation_text({"zone_state": [zone]})
    assert "zone_9" in text
    assert "Query query_zone_state" in text or "enemy_main" in text
    assert zone["enemy_information_age_seconds"] == 20.125


def test_zone_contents_label_each_side_in_the_cell_not_only_column_header():
    # Reproduce the final input of the thinking match: our mixed army was
    # previously described by the model as an enemy defensive army.
    zone = {"zone_id": "zone_15", "zone_role": "enemy_main", "known_owner": "unconfirmed",
            "vision_state": "visible",
            "own_contents": {"units": {"marine": 13, "siege_tank": 4, "medivac": 2}, "buildings": {}},
            "visible_enemy_contents": {"units": {"scv": 1}, "buildings": {"supply_depot": 2}},
            "last_seen_enemy_contents": {"units": {}, "buildings": {}},
            "enemy_information_age_seconds": None, "visible_enemy_weapon_in_range": False}
    original = copy.deepcopy(zone)
    text = render_observation_text({"zone_state": [zone]})
    row = next(line for line in text.splitlines() if line.startswith("zone_15 |"))
    cells = row.split(" | ")
    assert cells[4] == "OWN: units: marine 13, medivac 2, siege_tank 4"
    assert cells[5] == "ENEMY visible: units: scv 1; buildings: supply_depot 2"
    assert cells[6] == "ENEMY last seen: none"
    assert "marine 13" not in cells[5] and "siege_tank 4" not in cells[5]
    assert zone == original


def test_zone_cell_labels_preserve_unknown_and_other_races_without_inferred_enemy():
    zone = {"zone_id": "zone_2", "zone_role": "other_expansion", "known_owner": "unconfirmed",
            "vision_state": "fogged", "own_contents": {"units": {"zealot": 2}, "buildings": {}},
            "visible_enemy_contents": None,
            "last_seen_enemy_contents": {"units": {"zergling": 4}, "buildings": {}},
            "enemy_information_age_seconds": 12.5, "visible_enemy_weapon_in_range": None}
    text = render_observation_text({"zone_state": [zone]})
    assert "OWN: units: zealot 2 | ENEMY visible: unknown | ENEMY last seen: units: zergling 4 | 12.5 | unknown" in text
    assert zone["visible_enemy_contents"] is None


def test_training_building_summarize_counts_and_explain_blockers_without_per_order_progress():
    text = render_observation_text({
        "building": {"barracks": {"completed": 1, "under_construction": 2,
                                "worker_en_route": 1, "waiting_to_start": 3,
                                "waiting_for": "resources"}},
        "training": {"marine": {"in_production": 2, "waiting_to_produce": 4,
                               "order_progress": "2/8", "waiting_for": "producer_busy"}},
    })
    assert "barracks" in text
    assert "marine" in text
    assert "2/8" not in text


def test_compact_state_keeps_unrepresented_waiting_details():
    text = render_observation_text({
        "production_priority": [],
        "building": {"barracks": {"completed": 0, "under_construction": 0,
                                    "worker_en_route": 0, "waiting_to_start": 1,
                                    "waiting_for": "prerequisite:supply_depot"}},
        "training": {"marine": {"in_production": 0, "waiting_to_produce": 2,
                                  "waiting_for": "producer_busy"}},
    })
    assert "requires ready supply_depot" in text
    assert "all compatible production lanes are currently occupied" in text


def test_home_pool_count_has_one_text_owner():
    text = render_observation_text({
        "economy": {"worker_count": 12},
        "own_forces": {"workers": {"scv": 12}, "army": {"marine": 8},
                       "assigned": {}, "free": {"marine": 8}},
        "combat": {"group_0": {"style": "defend", "target": "zone_0",
                                  "status": "active", "phase": "guarding",
                                  "alive": {"marine": 8}, "assigned": False}},
    })
    assert "Living workers:" not in text
    assert text.count("Available for dispatch from home pool: marine 8") == 1
    assert "Available home members:" not in text
    assert "group_0:" in text
    assert "Nearest zone: unknown" in text
    assert "Current order: defend zone_0 — active" in text


def test_groups_sorted_numerically_and_scout_route_remains_ordered():
    groups = {name: {"alive": {}, "phase": "executing"} for name in ["group_10", "group_2", "group_0"]}
    text = render_observation_text({"combat": groups,
                                  "scouting": {"scv": {"route": ["zone_9", "zone_2", "zone_5"],
                                                       "moving_to": "zone_2", "waypoint_index": 1,
                                                       "assigned": True}}})
    assert text.index("group_0:") < text.index("group_2:") < text.index("group_10:")
    assert "group_0: none" in text
    assert "Route: zone_9 -> zone_2 -> zone_5" in text
    assert "Moving to: zone_2" in text and "Waypoint index: 1" in text


def test_automatic_attack_to_defend_transition_is_explicit():
    text = render_observation_text({"combat": {"group_3": {
        "style": "defend", "target": "zone_14", "status": "active",
        "phase": "holding_after_attack", "alive": {"roach": 5, "zergling": 6},
        "requested": {"roach": 5, "zergling": 10},
        "order_transition": {
            "from": "attack", "to": "defend",
            "reason": "target_confirmed_clear", "automatic": True,
        },
    }}})

    assert "group_3: roach 5, zergling 6" in text
    assert "Nearest zone: unknown" in text
    assert "Current activity: holding at zone_14" in text
    assert "Previous order: attack zone_14 — completed" in text
    assert "Current order: defend zone_14" not in text


def test_race_specific_capabilities_and_extra_nested_fields_are_not_dropped():
    text = render_observation_text({
        "abilities": {"inject_ready": 2, "queens": [{"id": "queen_1", "energy": 36.125}]},
        "combat": {"group_1": {"alive": {"zealot": 4}, "custom": {"warp": {"ready": True}}}},
        "structures": [{"id": "nexus_0", "type": "nexus", "energy": 50}],
        "research": {"terran_infantry_weapons_level_1": "completed", "stimpack": "waiting_for:resources"},
    })
    for phrase in ["Inject ready: 2", "Id: queen_1", "Energy: 36.12",
                   "zealot 4", "Warp:", "Ready: yes", "nexus_0 | nexus", "Energy: 50"]:
        assert phrase in text
    assert "terran_infantry_weapons_level_1: completed" in text
    assert "stimpack: waiting_for:resources" in text
    assert "{" not in text


def test_feedback_preserves_rejected_counts_and_group_events_without_json():
    feedback = {"receipts": [{"action": "combat", "result": "rejected",
                              "target": "zone_12", "reason": "insufficient_units",
                              "details": {"requested": {"marine": 8},
                                          "available": {"marine": 3}, "missing": {"marine": 5}}}],
                "events": [{"type": "combat_ended", "group": "group_2", "reason": "withdrawn"}]}
    original = copy.deepcopy(feedback)
    text = render_feedback_text(feedback)
    for phrase in ["1. combat: rejected", "Target: zone_12", "Reason: insufficient_units",
                   "Requested: marine 8", "Available: marine 3",
                   "Missing: marine 5", "combat_ended", "Group: group_2", "Reason: withdrawn"]:
        assert phrase in text
    assert "{" not in text and "}" not in text
    assert feedback == original


def test_briefing_omits_accepted_events_but_keeps_execution_changes():
    text = render_observation_text({"recent_events": [
        {"type": "demand_accepted", "action": "train", "target": "marine", "count": 2},
        {"type": "train_completed", "target": "marine", "count": 1},
    ]})
    assert "demand_accepted" not in text
    assert "train_completed" in text


def test_production_capacity_does_not_repeat_unit_technology_catalog():
    text = render_observation_text({
        "game": {"race": "terran"},
        "production": [{"facility": "barracks", "ready_grounded": 1,
                        "techlab_hosts": 0, "reactor_hosts": 0, "capacity": 1,
                        "occupied_slots": 0, "free_slots": 1, "queue_capacity": 5,
                        "queued_orders": 0, "free_queue_positions": 5,
                        "free_techlab_slots": 0}],
        "building": {"barracks": {"completed": 1}},
        "training": {},
        "available_targets": {"build": [], "train": ["marine", "reaper"],
                              "research": [], "morph_townhall": []},
    })
    assert "Training by producer type" not in text
    assert "Occupied slots" not in text and "Queued orders" not in text
    assert "Unit technology:" not in text
    assert "Train: marine, reaper" in text


def test_json_records_capture_the_actual_plain_messages(tmp_path):
    env = Environment(FakeBackend(), record_dir=tmp_path)
    try:
        obs = env.reset()
        messages = platform_turn_messages(env.latest_observation, env.latest_feedback, env.config.race if env.config else "terran")
        reply = [{"name": "advance", "arguments": {"seconds": 5}}]
        drive(env, reply, agent_context={"messages": messages, "assistant_content": json.dumps(reply)})
        path = env.record_path
    finally:
        env.close()
    from sc2bench_env.recording.reader import read_episode
    transcript = read_episode(path)
    assert transcript["messages"] == messages
    assert "Supply: 12/13" in transcript["messages"][1]["content"]
    assert '"supply_used"' not in transcript["messages"][1]["content"]
    assert env.trajectory()["steps"][0]["observation"]["economy"] == obs.to_dict()["economy"]
