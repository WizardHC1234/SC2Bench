"""Acceptance checks for the Brief OBS / Group / tool-transaction plan."""
from __future__ import annotations

from sc2bench_env import Environment, ToolCall
from sc2bench_env.adapters.llm import LLMAdapter
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.observations import (
    agent_observation_view,
    render_brief_observation_text,
)
from sc2bench_env.interface.tools import tool_specs
from tests.helpers.brief_obs_samples import STAGE_SAMPLES, terran_mid
from tests.helpers.drive import drive, query


def test_brief_mid_game_stays_in_soft_length_band():
    text = render_brief_observation_text(terran_mid())
    # Soft monitor from the plan (~700-1000 tokens ≈ 2300-3250 English chars).
    assert 1800 <= len(text) <= 4500
    assert "[Decision Request]" not in text
    assert "query_" not in text
    assert "ENEMY WEAPON IN RANGE" in text


def test_brief_hides_full_topology_but_read_tools_cover_it():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        public = agent_observation_view(env.latest_observation)
        brief = render_brief_observation_text(public.to_dict())
        assert "map_topology" not in brief.lower()
        assert public.map_topology == {}
        overview = query(env, ToolCall("query_map_overview", {}))
        zones = query(env, ToolCall("query_zone_state", {"zone_ids": ["zone_0", "zone_15"]}))
        assert overview["map_topology"]["zones"]
        assert zones["zones"]
        assert overview["as_of_game_time"] == zones["as_of_game_time"]
    finally:
        env.close()


def test_tool_turn_accumulates_actions_until_final_advance():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        env.backend.units["marine"] = 8
        turn = env.begin_tool_turn()
        first = turn.call(ToolCall(
            "dispatch_group",
            {"source": "group_0", "order": "attack", "target": "zone_15", "units": {"marine": 3}},
        ))
        second = turn.call(ToolCall(
            "dispatch_group",
            {"source": "group_0", "order": "attack", "target": "zone_14", "units": {"marine": 2}},
        ))
        assert first.status == "staged"
        assert second.status == "staged"
        ready = turn.call(ToolCall("advance", {"seconds": 1}))
        assert ready.status == "decision_ready"
        batch = turn.finish()
        obs, feedback, _, _ = env.step(batch)
        assert len([row for row in feedback.receipts if row.result == "accepted"]) >= 2
        assert {"group_0", "group_1", "group_2"} <= set(obs.combat)
        assert obs.game.game_time_seconds >= 1
    finally:
        env.close()


def test_call_many_requires_advance_last_and_closes_turn():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        turn = env.begin_tool_turn()
        results = turn.call_many([
            ToolCall("query_army_state", {}),
            ToolCall("advance", {"seconds": 1}),
        ])
        assert results[0].status == "ok"
        assert results[1].status == "decision_ready"
        batch = turn.finish()
        env.step(batch)
        stale = turn.call(ToolCall("query_army_state", {}))
        assert stale.status == "rejected"
        assert stale.data["error"]["code"] == "stale_turn"
    finally:
        env.close()


def test_reinforce_ten_times_keeps_one_outbound_group():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        env.backend.units["marine"] = 20
        drive(env, [
            {"name": "dispatch_group", "arguments": {
                "source": "group_0", "order": "attack", "target": "zone_15", "units": {"marine": 2},
            }},
            {"name": "advance", "arguments": {"seconds": 1}},
        ])
        for _ in range(10):
            obs, feedback, _, _ = drive(env, [
                {"name": "reinforce_group", "arguments": {
                    "source": "group_0", "destination": "group_1", "units": {"marine": 1},
                }},
                {"name": "advance", "arguments": {"seconds": 1}},
            ])
            assert feedback.receipts[0].result == "accepted"
        assert set(obs.combat) == {"group_0", "group_1"}
        assert obs.combat["group_1"]["alive"]["marine"] == 12
    finally:
        env.close()


def test_merge_invalid_group_is_atomic():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        env.backend.units["marine"] = 8
        drive(env, [
            {"name": "dispatch_group", "arguments": {
                "source": "group_0", "order": "attack", "target": "zone_15", "units": {"marine": 4},
            }},
            {"name": "advance", "arguments": {"seconds": 1}},
        ])
        before = dict(env.latest_observation.combat["group_1"]["alive"])
        obs, feedback, _, _ = drive(env, [
            {"name": "merge_groups", "arguments": {
                "destination": "group_1", "sources": ["group_9"],
            }},
            {"name": "advance", "arguments": {"seconds": 1}},
        ])
        assert feedback.receipts[0].result == "rejected"
        assert obs.combat["group_1"]["alive"] == before
    finally:
        env.close()


def test_prompt_and_tool_schema_keep_contract_without_per_turn_tutorial():
    prompt = LLMAdapter(race="terran").system_prompt()
    assert "Observation contract:" in prompt
    assert "Call advance exactly once as the final tool call" in prompt
    assert "Brief Observation covers ordinary economy" in prompt
    names = {spec.name for spec in tool_specs("terran")}
    assert {
        "query_production_state", "query_army_state", "merge_groups",
        "dispatch_group", "attack_group", "defend_group", "advance",
    } <= names
    dispatch = next(spec for spec in tool_specs("terran") if spec.name == "dispatch_group")
    assert set(dispatch.parameters["required"]) == {"source", "units", "order", "target"}
    user = render_brief_observation_text(STAGE_SAMPLES[("terran", "early")]())
    assert "[Decision Request]" not in user
    assert "[Decision]" not in user
    assert "Staged actions: none" not in user
