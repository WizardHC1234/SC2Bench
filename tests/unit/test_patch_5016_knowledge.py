"""Phase-1 checks for 5.0.16 knowledge values and cross-race queries."""
from tests.helpers.drive import query
from sc2bench_env.interface.tools import ToolCall

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.catalog.registry import get_target
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.platform_rules import ZERG_GAME_RULES
from agents.llm_agent.tools import tool_schemas
from sc2bench_env.interface.tools import KNOWLEDGE_TOOLS
from sc2bench_env.runtime.query_tools import run_query as execute_tool


def _knowledge_schemas():
    return {
        item["function"]["name"]: item["function"]["parameters"]
        for item in tool_schemas("zerg")
        if item["function"]["name"] in KNOWLEDGE_TOOLS
    }


def test_knowledge_tools_accept_all_three_races():
    schemas = _knowledge_schemas()
    assert set(schemas) == set(KNOWLEDGE_TOOLS)
    for name, parameters in schemas.items():
        assert parameters["properties"]["race"]["enum"] == ["terran", "protoss", "zerg"]
        assert "race" in parameters["required"]


def test_queries_use_the_requested_race_not_the_episode_race():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset(EpisodeConfig(race="terran", game_time_limit_seconds=20))
        queen = query(env, ToolCall("query_unit_data", {"race": "zerg", "names": ["queen"]}))["results"][0]
        adept = query(env, ToolCall("query_unit_data", {"race": "protoss", "names": ["adept"]}))["results"][0]
        missing = query(env, ToolCall("query_unit_data", {"names": ["marine"]}))
        invalid = execute_tool("query_race_data", {"race": "random"})
        assert queen["name"] == "queen"
        assert "inject_larva" in queen["description"]
        assert "not an action" not in queen["description"]
        assert adept["time_seconds"] == 33
        assert missing["error"]["code"] == "invalid_arguments"
        assert "race" in missing["error"]["message"]
        assert "race must be terran, protoss or zerg" in invalid["error"]
    finally:
        env.close()


def test_patch_5016_catalog_and_fake_supply():
    from sc2bench_env.runtime.query_tools import query_building_data

    assert get_target("command_center").minerals == 300
    assert get_target("planetary_fortress").minerals == 250
    assert get_target("planetary_fortress").vespene == 150
    assert get_target("ghost").supply == 2
    assert get_target("reaper").base_time_seconds == 34
    assert get_target("adept", race="protoss").base_time_seconds == 33
    hatchery = get_target("hatchery", race="zerg")
    nexus = get_target("nexus", race="protoss")
    assert "4 supply" in hatchery.description
    assert "6 supply" not in hatchery.description
    assert "chrono_boost" in nexus.description
    assert "not an action" not in nexus.description
    assert query_building_data(["hatchery"], race="zerg")["results"][0]["food_provided"] == 4
    assert "provides 4 supply" not in ZERG_GAME_RULES
    assert "exact amount" in ZERG_GAME_RULES
    assert "adds 6 supply" not in ZERG_GAME_RULES

    backend = FakeBackend()
    backend.start_episode(EpisodeConfig(race="zerg", game_time_limit_seconds=30))
    assert backend.supply_cap == 12
    backend._finish_building("hatchery")
    assert backend.supply_cap == 16
