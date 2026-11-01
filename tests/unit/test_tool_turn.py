"""Tool loop contract: one call, one result, advance closes the decision."""
from dataclasses import replace

import pytest

from sc2bench_env import AgentInput, AgentTurn, Environment, EpisodeConfig, ToolCall
from sc2bench_env.adapters.llm import LLMAdapter
from sc2bench_env.interface.actions import DecisionBatch
from sc2bench_env.interface.actions import parse_decision
from sc2bench_env.interface.platform_prompt import default_prompt_parts, render_prompt
from sc2bench_env.interface.tools import ToolSpec
from sc2bench_env.runtime.tool_turn import ToolTurnError
from agents.llm_agent.agent import LLMAgent
from agents.llm_agent.tools import tool_schemas


def _env():
    env = Environment(record_trajectory=False)
    env.reset(EpisodeConfig())
    return env


def test_queries_and_actions_interleave_until_advance():
    env = _env()
    turn = env.begin_tool_turn()
    overview = turn.call(ToolCall("query_map_overview", {}))
    assert overview.status == "ok" and "map_topology" in overview.data
    staged = turn.call(ToolCall("build", {"target": "supply_depot"}))
    assert staged.status == "staged"
    assert staged.data["effect"] == {
        "added": 1, "unfinished_before": 0, "unfinished_after": 1,
    }
    again = turn.call(ToolCall("query_zone_state", {"zone_ids": ["zone_0"]}))
    assert again.status == "ok"
    ready = turn.call(ToolCall("advance", {"seconds": 5}))
    assert ready.status == "decision_ready" and ready.decision_boundary is True
    assert ready.data["effect"]["submitted_actions"] == 1
    batch = turn.finish()
    assert isinstance(batch, DecisionBatch)
    assert env.task_manager.active_demands() == []
    env.step(batch)
    assert len(env.task_manager.active_demands()) == 1
    env.close()


def test_action_results_expose_cumulative_effect_and_reject_before_advance():
    env = _env()
    turn = env.begin_tool_turn()
    first = turn.call(ToolCall("train", {"target": "scv", "count": 2}))
    second = turn.call(ToolCall("train", {"target": "scv", "count": 3}))
    rejected = turn.call(ToolCall(
        "dispatch_group", {
            "source": "group_0", "target": "zone_1",
            "units": {"marine": 1}, "order": "attack",
        },
    ))
    assert first.data["effect"]["unfinished_after"] == 2
    assert second.data["effect"]["added"] == 3
    assert second.data["effect"]["unfinished_after"] == 5
    assert rejected.status == "rejected"
    assert rejected.data["effect"]["missing"] == {"marine": 1}

    ready = turn.call(ToolCall("advance", {"seconds": 5}))
    assert ready.data["effect"]["submitted_actions"] == 2
    batch = turn.finish()
    assert [action.action for action in batch.actions] == ["train", "train"]
    _observation, feedback, _terminated, _info = env.step(batch)
    assert feedback.receipts_delivered is True
    env.close()


def test_bounded_cancel_reports_queue_revision_before_advance():
    env = _env()
    env.step(parse_decision([
        {"name": "train", "arguments": {"target": "marine", "count": 5}},
        {"name": "advance", "arguments": {"seconds": 1}},
    ]))

    turn = env.begin_tool_turn()
    result = turn.call(ToolCall("cancel", {
        "target_action": "train", "target": "marine", "count": 2,
    }))

    assert result.status == "staged"
    assert result.data["request"]["count"] == 2
    assert result.data["effect"] == {
        "removed_unstarted": 2,
        "remaining_unstarted": 3,
        "protected_started_or_paid": 0,
        "requested_count": 2,
        "unremoved_count": 0,
        "unfinished_before": 5,
        "unfinished_after": 3,
    }
    assert result.data["execution"] == "not_started_until_advance"
    env.close()


def test_cancel_can_reduce_work_staged_earlier_in_same_decision():
    env = _env()
    turn = env.begin_tool_turn()
    turn.call(ToolCall("train", {"target": "marine", "count": 5}))
    result = turn.call(ToolCall("cancel", {
        "target_action": "train", "target": "marine", "count": 2,
    }))

    assert result.status == "staged"
    assert result.data["effect"]["unfinished_before"] == 5
    assert result.data["effect"]["unfinished_after"] == 3
    turn.call(ToolCall("advance", {"seconds": 1}))
    batch = turn.finish()
    env.step(batch)
    assert env.task_manager.production_priority_summary()[0]["remaining"] == 3
    env.close()


def test_action_noop_is_returned_immediately_and_not_committed_again():
    env = _env()
    turn = env.begin_tool_turn()
    first = turn.call(ToolCall("research", {"target": "stimpack"}))
    duplicate = turn.call(ToolCall("research", {"target": "stimpack"}))
    assert first.status == "staged"
    assert duplicate.status == "no_op"
    assert duplicate.data["effect"]["reason"] == "research_already_active"
    assert "added" not in duplicate.data["effect"]
    assert duplicate.data["effect"]["unfinished_after"] == 1
    turn.call(ToolCall("advance", {"seconds": 5}))
    batch = turn.finish()
    assert len(batch.actions) == 1
    env.close()


def test_call_many_keeps_order_and_advance_must_be_last():
    env = _env()
    turn = env.begin_tool_turn()
    results = turn.call_many((
        ToolCall("query_army_state", {}),
        ToolCall("build", {"target": "supply_depot"}),
        ToolCall("advance", {"seconds": 5}),
    ))
    assert [result.status for result in results] == ["ok", "staged", "decision_ready"]
    assert [call["name"] for call in turn.finish().to_dicts()] == ["build", "advance"]
    env.close()

    env = _env()
    turn = env.begin_tool_turn()
    results = turn.call_many((
        ToolCall("advance", {"seconds": 5}),
        ToolCall("query_army_state", {}),
    ))
    assert results[-1].status == "rejected"
    with pytest.raises(ToolTurnError, match="turn_invalid"):
        turn.finish()
    env.close()


def test_missing_advance_is_a_protocol_error_and_discards_staged_work():
    env = _env()
    turn = env.begin_tool_turn()
    assert turn.call(ToolCall("build", {"target": "supply_depot"})).status == "staged"
    with pytest.raises(ToolTurnError) as error:
        turn.finish()
    assert error.value.code == "missing_advance"
    assert env.task_manager.active_demands() == []
    env.close()


def test_second_advance_invalidates_the_turn():
    env = _env()
    turn = env.begin_tool_turn()
    assert turn.call(ToolCall("advance", {"seconds": 5})).status == "decision_ready"
    closed = turn.call(ToolCall("advance", {"seconds": 5}))
    assert closed.status == "rejected" and closed.data["error"]["code"] == "turn_closed"
    with pytest.raises(ToolTurnError) as error:
        turn.finish()
    assert error.value.code == "turn_invalid"
    env.close()


def test_raw_array_does_not_step():
    env = _env()
    with pytest.raises(TypeError):
        env.step([{"name": "advance", "arguments": {"seconds": 5}}])
    assert env.latest_observation.game.game_time_seconds == 0
    env.close()


def test_unknown_and_race_gated_tools():
    env = _env()
    turn = env.begin_tool_turn()
    unknown = turn.call(ToolCall("not_a_tool", {}))
    assert unknown.data["error"]["code"] == "unknown_tool"
    chrono = turn.call(ToolCall("chrono_boost", {}))
    assert chrono.data["error"]["code"] == "unavailable_for_race"
    extra = turn.call(ToolCall("query_zone_state", {"zone_ids": ["zone_0"], "extra": 1}))
    assert extra.data["error"]["code"] == "invalid_arguments"
    env.close()


def test_protoss_chrono_is_an_action_not_an_unknown_query():
    env = Environment(record_trajectory=False)
    env.reset(EpisodeConfig(race="protoss"))
    turn = env.begin_tool_turn()
    result = turn.call(ToolCall("chrono_boost", {}))
    assert result.status == "staged"
    assert "unknown_tool" not in str(result.data)
    env.close()


def test_trimmed_specs_are_what_the_model_receives():
    spec = ToolSpec(
        name="advance", kind="action", description="advance only",
        parameters={"type": "object", "properties": {"seconds": {"type": "number"}}, "required": ["seconds"]},
    )
    schemas = tool_schemas((spec,))
    assert [row["function"]["name"] for row in schemas] == ["advance"]

    seen = {}

    def call_llm(messages, tools=None):
        seen["tools"] = tools
        return {"content": "go", "tool_calls": [{"name": "advance", "arguments": {"seconds": 5}}]}

    env = _env()
    turn = env.begin_tool_turn()
    agent = LLMAgent(call_llm, verbose=False, skill_name="none")
    request = AgentInput(env.latest_observation, None, (spec,), turn.call)
    result = agent(request)
    assert isinstance(result, AgentTurn)
    assert [row["function"]["name"] for row in seen["tools"]] == ["advance"]
    assert turn.finish().advance.seconds == 5
    env.close()


def test_prompt_parts_keep_order_and_allow_one_replacement():
    parts = default_prompt_parts("terran")
    rendered = render_prompt(parts)
    headings = [
        "1. Role and objective", "2. Game basics", "3. Reading Observation",
        "4. Tool interaction", "5. Platform execution model", "6. Decision guidance",
    ]
    assert [rendered.index(item) for item in headings] == sorted(rendered.index(item) for item in headings)
    assert "ReAct" not in rendered
    replaced = replace(parts, decision_guidance="Custom guidance only.")
    custom = render_prompt(replaced)
    assert "Custom guidance only." in custom
    assert parts.role_and_objective in custom
    assert parts.game_rules in custom
    assert parts.tool_interaction in custom
    for race in ("terran", "protoss", "zerg"):
        text = LLMAdapter(race=race).system_prompt()
        assert text == render_prompt(default_prompt_parts(race))
        assert "parameters" not in text.split("6. Decision guidance", 1)[0][-40:]
