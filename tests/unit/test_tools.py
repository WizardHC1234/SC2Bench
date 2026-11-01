"""Read, Knowledge and Action tools: query current facts, never advance the game."""
from tests.helpers.drive import query
from sc2bench_env.interface.tools import ToolCall

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.config import EpisodeConfig
from agents.llm_agent.tools import tool_schemas
from sc2bench_env.interface.tools import ACTION_TOOLS, KNOWLEDGE_TOOLS, READ_TOOLS, ToolCall
from sc2bench_env.runtime.query_tools import run_query as execute_tool


def test_tool_schemas_are_compact_and_cover_all_tools():
    schemas = tool_schemas()
    names = [item["function"]["name"] for item in schemas]
    assert names == list(READ_TOOLS) + list(KNOWLEDGE_TOOLS) + list(ACTION_TOOLS)
    encoded = str(schemas)
    assert "siege_tank" not in encoded
    assert "stimpack" not in encoded
    assert "marine" not in encoded


def test_knowledge_tools_use_catalog_and_do_not_move_time():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=20))
        before = env.backend.snapshot().game_time_seconds
        tank = query(env, ToolCall("query_unit_data", {"race": "terran", "names": ["siege_tank"]}))["results"][0]
        path = query(env, ToolCall("query_prerequisite_path", {"race": "terran", "target": "siege_tank"}))
        race = query(env, ToolCall("query_race_data", {"race": "terran"}))
        orbital = query(env, ToolCall("query_building_data", {"race": "terran", "names": ["orbital_command"]}))["results"][0]
        assert tank["name"] == "siege_tank"
        assert tank["minerals"] == 150 and tank["vespene"] == 125
        assert tank["produced_at"] == "factory"
        assert "factory_techlab" in path["path"]
        assert "siege_tank" in race["units"]
        assert orbital["morph_from"] == "command_center"
        assert env.backend.snapshot().game_time_seconds == before
        assert env.task_manager.active_demands() == []
    finally:
        env.close()


def test_read_tools_match_structured_observation_and_keep_fog():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        obs = env.reset()
        overview = query(env, ToolCall("query_map_overview"))
        assert overview["map_topology"] == obs.map_topology
        assert len(overview["map_topology"]["zones"]) == len(obs.zone_state)
        route = query(env, ToolCall("query_route", {"from_zone": "zone_0", "to_zone": "zone_2"}))
        assert route["path"] == ["zone_0", "zone_1", "zone_2"]
        assert route["total_distance"] == 40
        enemy = query(env, ToolCall("query_zone_state", {"zone_ids": ["zone_15"]}))["zones"][0]
        assert enemy["vision_state"] == "fogged"
        assert enemy["visible_enemy_contents"] == {"units": {}, "buildings": {}}
        all_zones = query(env, ToolCall("query_zone_state", {}))
        assert len(all_zones["zones"]) == len(obs.zone_state)
        unknown = query(env, ToolCall("query_zone_state", {"zone_ids": ["zone_missing"]}))
        assert unknown["unknown_zone_ids"] == ["zone_missing"]
    finally:
        env.close()


def test_action_tools_are_not_executed_by_the_environment():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        result = query(env, ToolCall("build", {"target": "supply_depot"}))
        assert result["action"] == "build"
        assert result["effect"]["added"] == 1
        assert env.task_manager.active_demands() == []
        unknown = execute_tool("not_a_tool")
        assert "unknown_tool" in unknown["error"]
    finally:
        env.close()
