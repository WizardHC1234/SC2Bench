"""Compact turn briefing: relevant zones and currently available targets."""
from tests.helpers.drive import drive
from agents.llm_agent.prompts import platform_turn_messages

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FAKE_ZONE_COUNT, FakeBackend
from sc2bench_env.runtime.observation_builder import available_targets, compact_observation, relevant_zone_ids
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.observations import (
    agent_observation_view, observation_lines, render_brief_observation_text, BRIEFING_SECTIONS,
    OBSERVATION_SECTIONS,
)


def test_opening_targets_follow_tech_not_minerals():
    names = available_targets(
        race="terran",
        building={"command_center": {"completed": 1}},
        structures=[{"id": "townhall_0", "type": "command_center"}],
    )
    assert "supply_depot" in names["build"]
    assert "refinery" in names["build"]
    assert "command_center" in names["build"]
    assert "barracks" not in names["build"]
    assert "scv" in names["train"]
    assert "marine" not in names["train"]
    assert names["research"] == []
    assert names["morph_townhall"] == []
    with_depot = available_targets(
        race="terran",
        building={"command_center": {"completed": 1}, "supply_depot": {"completed": 1}},
    )
    assert "barracks" in with_depot["build"]
    with_barracks = available_targets(
        race="terran",
        building={"command_center": {"completed": 1}, "supply_depot": {"completed": 1},
                  "barracks": {"completed": 1}},
    )
    assert "orbital_command" in with_barracks["morph_townhall"]
    assert "marine" in with_barracks["train"]


def test_finished_research_leaves_available_tech_and_unlocks_the_next_level():
    building = {
        "hatchery": {"completed": 1},
        "lair": {"completed": 1},
        "spawning_pool": {"completed": 1},
        "evolution_chamber": {"completed": 1},
    }
    before = available_targets(race="zerg", building=building, units={"zergling": 4})
    assert "metabolic_boost" in before["research"]
    assert "melee_attacks_1" in before["research"]
    assert "melee_attacks_2" not in before["research"]
    assert "zergling" in before["train"]
    after = available_targets(
        race="zerg",
        building=building,
        units={"zergling": 4},
        research={"metabolic_boost": "completed", "burrow": "in_progress", "melee_attacks_1": "completed"},
    )
    assert "metabolic_boost" not in after["research"]
    assert "burrow" not in after["research"]
    assert "melee_attacks_1" not in after["research"]
    assert "pneumatized_carapace" in after["research"]
    assert "melee_attacks_2" in after["research"]
    assert "zergling" in after["train"]


def test_morph_unit_is_listed_only_while_the_source_is_alive():
    building = {
        "hatchery": {"completed": 1},
        "spawning_pool": {"completed": 1},
        "baneling_nest": {"completed": 1},
    }
    assert "baneling" in available_targets(race="zerg", building=building, units={"zergling": 6})["train"]
    assert "baneling" not in available_targets(race="zerg", building=building, units={})["train"]


def test_marine_appears_only_after_barracks_is_ready():
    before = available_targets(race="terran", building={"command_center": {"completed": 1}})
    after = available_targets(
        race="terran",
        building={"command_center": {"completed": 1}, "barracks": {"completed": 1}},
    )
    assert "marine" not in before["train"]
    assert "marine" in after["train"]


def test_compact_briefing_keeps_owned_and_enemy_bases_without_neighbors():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        obs = env.reset(EpisodeConfig(game_time_limit_seconds=20))
        payload = obs.to_dict()
        compact = compact_observation(payload)
        ids = compact["relevant_zone_ids"]
        assert "zone_0" in ids and "zone_15" in ids
        assert "zone_1" not in ids and "zone_14" not in ids
        assert len(ids) < FAKE_ZONE_COUNT
        assert [row["zone_id"] for row in compact["zone_state"]] == ids
        text = "\n".join(observation_lines(obs))
        assert "[Map Topology]" not in text
        assert "[Tech-Available Targets]" in text
        assert "Build: " in text and "supply_depot" in text
        assert "Train: " in text and "scv" in text
        assert "Time 00:00/00:20" in text and "own race terran" in text
        assert payload["map_topology"]["zones"]
        assert len(payload["zone_state"]) == FAKE_ZONE_COUNT
    finally:
        env.close()


def test_empty_unconfirmed_zones_are_omitted_until_queried():
    observation = {
        "zone_state": [{
            "zone_id": "zone_9", "zone_role": "other_expansion",
            "known_owner": "unconfirmed", "vision_state": "fogged",
            "own_contents": {"units": {}, "buildings": {}},
            "visible_enemy_contents": {"units": {}, "buildings": {}},
            "last_seen_enemy_contents": {"units": {}, "buildings": {}},
            "enemy_information_age_seconds": None,
            "visible_enemy_weapon_in_range": False,
        }],
        "map_topology": {"zones": []},
    }
    assert relevant_zone_ids(observation) == []
    compact = compact_observation(observation)
    assert compact["zone_state"] == []


def test_routine_zone_changes_and_neighbors_do_not_expand_briefing():
    zones = [
        {"zone_id": "zone_0", "known_owner": "self", "own_contents": {"units": {"scv": 9}}},
        {"zone_id": "zone_1", "known_owner": "unconfirmed", "own_contents": {}},
        {"zone_id": "zone_2", "known_owner": "unconfirmed", "own_contents": {}},
    ]
    previous = {"zone_state": [dict(zones[0], own_contents={"units": {"scv": 8}}), *zones[1:]]}
    observation = {
        "zone_state": zones,
        "map_topology": {"zones": [{"zone_id": "zone_0", "corridor_neighbors": [
            {"zone_id": "zone_1", "path_distance": 20}
        ]}]},
    }
    assert relevant_zone_ids(observation, previous) == ["zone_0"]


def test_briefing_sections_drop_static_topology():
    assert "map_topology" in dict(OBSERVATION_SECTIONS)
    assert "map_topology" not in dict(BRIEFING_SECTIONS)
    assert "available_targets" in dict(BRIEFING_SECTIONS)
    assert "relevant_zone_ids" not in dict(BRIEFING_SECTIONS)


def test_later_session_turn_keeps_self_contained_game_identity():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=600.0))
        opening = platform_turn_messages(env.latest_observation, env.latest_feedback, env.config.race if env.config else "terran")[1]["content"]
        assert "own race terran" in opening
        assert "Time 00:00/10:00" in opening

        drive(env, [{"name": "advance", "arguments": {"seconds": 5}}])
        later = platform_turn_messages(env.latest_observation, env.latest_feedback, env.config.race if env.config else "terran")[1]["content"]
        # Each decision remains self-contained; race and limit stay visible.
        assert "own race terran" in later and "enemy race " in later
        assert "Time 00:05/10:00;" in later
        assert "remaining)" not in later
        assert "Bank:" in later and "free " in later
        assert "[Decision Request]" not in later
        assert "[Decision]" not in later
    finally:
        env.close()


def test_text_compaction_does_not_mutate_complete_observation():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        obs = env.reset()
        payload = obs.to_dict()
        before = {
            "game": dict(payload["game"]),
            "resources": [dict(row) for row in payload["map_control"]["base_resources"]],
            "workers": dict(payload["own_forces"]["workers"]),
            "group_0": dict(payload["combat"]["group_0"]),
        }
        platform_turn_messages(env.latest_observation, env.latest_feedback, env.config.race if env.config else "terran")
        assert payload["game"] == before["game"]
        assert payload["map_control"]["base_resources"] == before["resources"]
        assert payload["own_forces"]["workers"] == before["workers"]
        assert payload["combat"]["group_0"] == before["group_0"]
    finally:
        env.close()


def test_public_agent_view_keeps_owned_base_resources_when_rendered_again():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        public = agent_observation_view(env.latest_observation)
        text = platform_turn_messages(
            public, env.latest_feedback, env.config.race if env.config else "terran",
        )[1]["content"]
        assert "Base resources:" in text
        assert "- zone_0: minerals " in text
        assert "gas structures " in text
        assert "free geysers " in text
    finally:
        env.close()


def test_brief_observation_marks_blockers_and_omitted_zones_without_duplicate_facilities():
    observation = {
        "game": {"race": "terran", "enemy_race": "terran", "status": "ongoing"},
        "economy": {"minerals": 50, "vespene": 0, "supply_used": 15,
                    "supply_cap": 15, "supply_left": 0, "army_supply": 4},
        "map_control": {"own_base_count": 1, "known_enemy_base_count": 1,
                        "unconfirmed_expansion_count": 2, "base_resources": [{
                            "zone_id": "zone_0", "minerals_remaining": 6000,
                            "vespene_remaining": 4000, "owned_gas_structure_count": 0,
                            "available_geyser_slots": 2,
                        }]},
        "available_targets": {"build": ["supply_depot"], "train": ["marine"],
                              "research": [], "morph_townhall": []},
        "production": [{"facility": "barracks", "ready_grounded": 1,
                        "capacity": 1, "free_slots": 0}],
        "production_priority": [{"action": "train", "target": "marine",
                                 "remaining": 4, "waiting_to_produce": 4,
                                 "in_production": 0, "state": "waiting_to_start",
                                 "waiting_for": "supply"}],
        "building": {
            "barracks": {"completed": 1, "under_construction": 0},
            "supply_depot": {"completed": 1, "under_construction": 0},
        },
        "zone_state": [{"zone_id": "zone_0", "zone_role": "own_main",
                        "known_owner": "self", "vision_state": "visible",
                        "own_contents": {}, "visible_enemy_contents": {},
                        "last_seen_enemy_contents": {},
                        "enemy_information_age_seconds": None,
                        "visible_enemy_weapon_in_range": False}],
        "own_forces": {}, "combat": {}, "abilities": {}, "recent_events": [],
    }
    text = render_brief_observation_text(observation)
    assert "SUPPLY BLOCKED" in text
    assert "Base resources:" in text and "- zone_0: minerals 6000" in text
    assert "4 items remaining" in text
    assert "waiting for insufficient available supply" in text or "waiting for supply" in text
    assert (
        "Map zones: self-owned 1; enemy-owned 1; unconfirmed 2; "
        "relevant shown 1/4"
    ) in text
    infrastructure = text.split("[Infrastructure]\n", 1)[1].split(
        "\n\n[Available Targets]", 1,
    )[0]
    assert "supply_depot: ready 1" in infrastructure
    assert "barracks: ready 1" in infrastructure
    assert "slot" not in text.lower()


def test_zerg_brief_separates_overlords_from_controllable_army():
    observation = {
        "game": {"race": "zerg", "enemy_race": "terran", "status": "ongoing"},
        "own_forces": {
            "army": {"zergling": 8, "overlord": 3},
            "assigned": {"zergling": 2, "overlord": 3},
            "free": {"zergling": 6},
        },
        "combat": {},
    }

    text = render_brief_observation_text(observation)

    assert "Total: zergling 8" in text
    assert "Total: overlord" not in text
    assert "Unavailable: zergling 2" in text
    assert "Non-combat support: overlord 3" in text


def test_brief_production_summarizes_cancellable_and_blocked_work():
    observation = {
        "game": {"race": "terran", "enemy_race": "zerg", "status": "ongoing"},
        "production_priority": [
            {"action": "train", "target": "marine", "remaining": 8,
             "in_production": 2, "waiting_to_produce": 6,
             "cancellable_count": 6, "state": "in_production",
             "waiting_for": "supply"},
            {"action": "build", "target": "factory", "remaining": 1,
             "cancellable_count": 1, "state": "waiting_to_start",
             "waiting_for": "resources"},
        ],
    }

    text = render_brief_observation_text(observation)

    assert "Not started and cancellable: 7" in text
    assert "Blocked work: supply 6; resources 1" in text
    assert "cancellable 6" in text
