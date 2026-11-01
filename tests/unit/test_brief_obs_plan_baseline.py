"""Plan step 1: Brief OBS field coverage and Group membership baselines."""
from __future__ import annotations

import pytest

from sc2bench_env.interface.observations import (
    agent_observation_view,
    render_brief_observation_text,
)
from sc2bench_env.runtime.observation_builder import compact_observation
from tests.helpers.brief_obs_samples import (
    STAGE_SAMPLES,
    free_plus_assigned,
    group_alive_total,
    living_army_counts,
)
from tests.helpers.drive import drive, query
from sc2bench_env import Environment, ToolCall
from sc2bench_env.backends.fake import FakeBackend


# Field destination table from the platform plan (section 5).
BRIEF_REQUIRED_MARKERS = (
    "[Game]",
    "[Economy]",
    "[Production]",
    "[Infrastructure]",
    "[Available Targets]",
    "[Army]",
    "[Relevant Zones]",
    "[Decision]",
)

BRIEF_CORE_PHRASES = (
    "Time ",
    "own race ",
    "enemy race ",
    "Bank:",
    "Supply:",
    "Workers (",
    "Income/min:",
    "Base resources:",
    "Map zones:",
    "Ongoing work:",
    "Buildings:",
    "Available build:",
)


@pytest.mark.parametrize("race,stage", list(STAGE_SAMPLES))
def test_stage_samples_render_required_brief_sections(race, stage):
    observation = STAGE_SAMPLES[(race, stage)]()
    text = render_brief_observation_text(observation)
    for marker in BRIEF_REQUIRED_MARKERS:
        assert marker in text, marker
    for phrase in BRIEF_CORE_PHRASES:
        assert phrase in text, phrase
    assert "map_topology" not in text.lower()
    assert "query_" not in text
    # Self-contained Game line each decision.
    assert f"own race {race}" in text
    assert "Time " in text and "remaining)" in text


@pytest.mark.parametrize("race,stage", list(STAGE_SAMPLES))
def test_stage_samples_preserve_full_fields_for_records_and_queries(race, stage):
    observation = STAGE_SAMPLES[(race, stage)]()
    compact = compact_observation(observation)
    assert "map_topology" not in compact
    assert observation["map_topology"]["zones"]
    assert len(compact["zone_state"]) <= len(observation["zone_state"])
    assert compact["game"]["race"] == race
    # Full combat diagnostics remain on the source observation.
    if stage != "early":
        outbound = next(
            row for name, row in observation["combat"].items() if name != "group_0"
        )
        assert "alive" in outbound


@pytest.mark.parametrize("race,stage", [("terran", "mid"), ("protoss", "mid"), ("zerg", "mid"),
                                        ("terran", "late")])
def test_group_alive_counts_are_covered_by_living_army(race, stage):
    observation = STAGE_SAMPLES[(race, stage)]()
    army = living_army_counts(observation)
    outbound = group_alive_total(observation)
    split = free_plus_assigned(observation)
    for unit, count in outbound.items():
        assert army.get(unit, 0) >= count, (unit, army, outbound)
    for unit, count in split.items():
        assert army.get(unit, 0) >= count, (unit, army, split)


def test_fake_backend_group_conservation_across_reinforce_and_merge():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        env.backend.units["marine"] = 20

        def living():
            forces = env.latest_observation.own_forces.to_dict()
            return int(forces["army"].get("marine", 0))

        def partition():
            combat = env.latest_observation.combat
            home = int((combat.get("group_0") or {}).get("alive", {}).get("marine", 0) or 0)
            free = int(env.latest_observation.own_forces.to_dict().get("free", {}).get("marine", 0) or 0)
            outbound = sum(
                int((row.get("alive") or {}).get("marine", 0) or 0)
                for name, row in combat.items() if name != "group_0"
            )
            return home or free, outbound

        drive(env, [
            {"name": "combat", "arguments": {
                "style": "attack", "target": "zone_15", "units": {"marine": 6},
            }},
            {"name": "combat", "arguments": {
                "style": "attack", "target": "zone_14", "units": {"marine": 4},
            }},
            {"name": "advance", "arguments": {"seconds": 1}},
        ])
        total = living()
        home, outbound = partition()
        assert home + outbound == total

        drive(env, [
            {"name": "combat", "arguments": {"group": "group_1", "units": {"marine": 3}}},
            {"name": "advance", "arguments": {"seconds": 1}},
        ])
        assert living() == total
        home, outbound = partition()
        assert home + outbound == total
        assert set(env.latest_observation.combat) == {"group_0", "group_1", "group_2"}

        drive(env, [
            {"name": "merge_groups", "arguments": {
                "destination": "group_1", "sources": ["group_2"],
            }},
            {"name": "advance", "arguments": {"seconds": 1}},
        ])
        assert living() == total
        home, outbound = partition()
        assert home + outbound == total
        assert set(env.latest_observation.combat) == {"group_0", "group_1"}
        assert env.latest_observation.combat["group_1"]["alive"]["marine"] == 13
    finally:
        env.close()


def test_agent_view_hides_topology_while_read_tools_keep_full_snapshot():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        full = env.latest_observation
        public = agent_observation_view(full)
        brief = render_brief_observation_text(public.to_dict())
        assert public.map_topology == {}
        assert "Time " in brief and "own race terran" in brief
        overview = query(env, ToolCall("query_map_overview", {}))
        assert overview["map_topology"]["zones"]
        army = query(env, ToolCall("query_army_state", {}))
        production = query(env, ToolCall("query_production_state", {}))
        assert army["as_of_game_time"] == production["as_of_game_time"]
    finally:
        env.close()
