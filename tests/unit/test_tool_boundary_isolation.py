"""External agents cannot change the frozen turn through inputs or results."""
from copy import deepcopy

import pytest

from sc2bench_env import Environment, EpisodeConfig, ToolCall
from sc2bench_env.interface.tools import tool_specs
from sc2bench_env.runtime.tool_turn import ToolTurn


@pytest.mark.parametrize("race", ["terran", "protoss", "zerg"])
def test_nested_query_result_mutation_does_not_write_into_frozen_snapshot(race):
    env = Environment("fake", record_trajectory=False)
    try:
        env.reset(EpisodeConfig(race=race))
        turn = env.begin_tool_turn()
        first = turn.call(ToolCall("query_map_overview"))
        expected = deepcopy(first.data)
        first.data["map_topology"]["zones"][0]["zone_id"] = "changed_by_caller"
        first.data["map_topology"]["zones"].clear()
        assert turn.call(ToolCall("query_map_overview")).data == expected
    finally:
        env.close()


def test_tool_turn_takes_its_own_snapshot_copy():
    snapshot = {"game": {"game_time_seconds": 10}, "map_topology": {"zones": [{"zone_id": "zone_0"}]}}
    turn = ToolTurn(specs=tool_specs("terran"), race="terran", observation=snapshot,
                    game_time_seconds=10, decision_index=0)
    snapshot["game"]["game_time_seconds"] = 999
    snapshot["map_topology"]["zones"].clear()
    result = turn.call(ToolCall("query_map_overview"))
    assert result.data["as_of_game_time"] == 10
    assert result.data["map_topology"]["zones"] == [{"zone_id": "zone_0"}]


@pytest.mark.parametrize("arguments", [None, 7, "invalid", [], ["not", "a mapping"]])
def test_invalid_argument_container_is_rejected_without_losing_staged_work(tmp_path, arguments):
    env = Environment("fake", record_dir=tmp_path)
    try:
        env.reset()
        turn = env.begin_tool_turn()
        assert turn.call(ToolCall("train", {"target": "scv", "count": 1})).status == "staged"
        result = turn.call(ToolCall("train", arguments))
        assert result.status == "rejected"
        assert result.data["error"]["code"] == "invalid_arguments"
        assert turn.call(ToolCall("advance", {"seconds": 1})).decision_boundary
        batch = turn.finish()
        assert len(batch.actions) == 1
        env.step(batch)
    finally:
        env.close()


@pytest.mark.parametrize("name", [None, [], {}, 7, "", " "])
def test_invalid_tool_name_returns_a_rejection(name):
    turn = ToolTurn(specs=tool_specs("terran"), race="terran", observation={},
                    game_time_seconds=0, decision_index=0)
    result = turn.call(ToolCall(name))
    assert result.status == "rejected"
    assert result.data["error"]["code"] == "invalid_arguments"


def test_mutating_request_and_serialized_call_cannot_rewrite_committed_trace():
    env = Environment("fake", record_trajectory=False)
    try:
        env.reset()
        turn = env.begin_tool_turn()
        arguments = {"route": ["zone_0", "zone_1"]}
        call = ToolCall("scout", arguments)
        result = turn.call(call)
        assert result.status == "staged"
        arguments["route"].clear()
        result.data["request"]["route"].clear()
        turn.call(ToolCall("advance", {"seconds": 1}))
        batch = turn.finish()
        assert batch.raw[0]["arguments"]["route"] == ["zone_0", "zone_1"]
        assert tuple(batch.actions[0].route) == ("zone_0", "zone_1")
    finally:
        env.close()
