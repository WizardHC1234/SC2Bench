"""Recording contract: race-bound prompts and one canonical session record."""
import json
from pathlib import Path

import pytest

from agents.llm_agent import LLMAgent
from agents.llm_agent.client import build_chat_payload
from sc2bench_env import Environment, ToolCall
from sc2bench_env.adapters.llm import LLMAdapter
from sc2bench_env.benchmark import AgentInput
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.platform_prompt import PromptParts, default_prompt_parts
from sc2bench_env.recording.reader import read_episode
from tests.helpers.drive import drive


def _note(name: str) -> dict:
    return {
        "tool_calls": [{"id": name, "name": name, "arguments": {}}],
        "content": f"The next fact I need is {name}.",
        "raw_content": "",
    }


def test_adapter_system_prompt_follows_the_bound_race():
    for race in ("terran", "protoss", "zerg"):
        text = LLMAdapter(race=race).system_prompt().lower()
        assert f"controlling {race}" in text
        for other in ("terran", "protoss", "zerg"):
            if other != race:
                assert f"controlling {other}" not in text
    custom = PromptParts(
        role_and_objective="Custom role for the harness.",
        game_rules="Custom rules.",
        observation_rules="Custom observation.",
        tool_interaction="Custom tools.",
        execution_rules="Custom execution.",
        decision_guidance="Custom guidance.",
    )
    rendered = LLMAdapter(race="zerg", prompt_parts=custom).system_prompt()
    assert "Custom role for the harness." in rendered
    assert "controlling zerg" not in rendered.lower()
    with pytest.raises(ValueError):
        LLMAdapter(race="random")


def test_ambiguous_adapter_prompt_call_is_gone():
    roots = [Path("src"), Path("agents")]
    for root in roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "system_prompt(race)" not in text
            assert "LLMAdapter().system_prompt(" not in text


def test_provider_payload_keeps_multi_call_replies():
    payload = build_chat_payload(
        model="deepseek-v4-flash", messages=[], temperature=0.5, thinking=False,
        tools=[{"type": "function"}],
    )
    assert "parallel_tool_calls" not in payload


def test_metrics_track_queries_actions_and_the_ending(tmp_path):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(race="zerg", game_time_limit_seconds=20))
        path = env.record_path / "metrics.json"
        running = json.loads(path.read_text(encoding="utf-8"))
        assert running["schema_version"] == "0.8"
        assert running["tool_protocol_version"] == "tool_turn_v3"
        assert running["tool_calls"] == []
        turn = env.begin_tool_turn()
        query = turn.call(ToolCall("query_race_data", {"race": "zerg"}))
        assert query.status == "ok"
        staged = turn.call(ToolCall("train", {"target": "drone", "count": 1}))
        assert staged.status == "staged"
        ready = turn.call(ToolCall("advance", {"seconds": 5}))
        assert ready.status == "decision_ready"
        env.step(turn.finish())
    finally:
        env.close()
    document = json.loads(path.read_text(encoding="utf-8"))
    states = [event["execution_state"] for event in document["tool_calls"]]
    assert states == ["completed", "staged", "decision_ready"]
    assert [event["name"] for event in document["tool_calls"]] == [
        "query_race_data", "train", "advance",
    ]
    assert document["summary"]["integrity"]["ok"] is True
    episode = read_episode(env.record_path)
    assert episode["tool_protocol_version"] == "tool_turn_v3"
    assert episode["metrics"]["episode_id"] == document["episode_id"]
    assert "model_calls" not in episode["session"]
    assert "decisions" not in episode["session"]
    assert "summary" not in episode["session"]
    assert episode["trajectory"] is None
    assert not (env.record_path / "trajectory.json").exists()
    assert not list(tmp_path.glob("run_*.json"))
    assert not list(tmp_path.glob("interactions.jsonl"))


def test_empty_content_tool_call_is_submitted_and_recorded(tmp_path):
    def call_llm(messages, tools=None):
        if not any(message.get("role") == "tool" for message in messages):
            return {
                "tool_calls": [{"id": "q", "name": "query_map_overview", "arguments": {}}],
                "content": "",
                "raw_content": "",
            }
        return {
            "tool_calls": [{"id": "a", "name": "advance", "arguments": {"seconds": 5}}],
            "content": "Nothing else is needed, so I advance.",
            "raw_content": "",
        }

    env = Environment(record_dir=tmp_path)
    agent = LLMAgent(call_llm, verbose=False)
    try:
        observation = env.reset(EpisodeConfig(game_time_limit_seconds=10))
        turn = env.begin_tool_turn()
        result = agent(AgentInput(observation, None, tuple(env.tool_specs()), turn.call))
        env.step(turn.finish(), agent_context=result.agent_context)
    finally:
        env.close()
    episode = read_episode(env.record_path)
    session_text = json.dumps(episode["messages"])
    metrics = episode["metrics"]
    assert "not_submitted" not in session_text
    assert "query_map_overview" in session_text
    assert "not_submitted" not in json.dumps(metrics)
    assert [event["name"] for event in metrics["tool_calls"]] == [
        "query_map_overview", "advance",
    ]
    assert any(
        message.get("role") == "user"
        and message.get("content", "").startswith("[Current Observation]")
        for message in episode["messages"]
    )


def test_extra_provider_calls_are_not_sent_to_the_platform(tmp_path):
    replies = [
        {
            "tool_calls": [
                {"id": "q1", "name": "query_map_overview", "arguments": {}},
                {"id": "q2", "name": "query_race_data", "arguments": {"race": "terran"}},
            ],
            "content": "I need the map and the race data.",
        },
        {
            "tool_calls": [
                {"id": "b", "name": "build", "arguments": {"target": "supply_depot"}},
                {"id": "t", "name": "train", "arguments": {"target": "marine", "count": 1}},
                {"id": "a", "name": "advance", "arguments": {"seconds": 5}},
            ],
            "content": "Build, train, then advance.",
        },
        {
            "tool_calls": [
                {"id": "b2", "name": "build", "arguments": {"target": "supply_depot"}},
                {"id": "t2", "name": "train", "arguments": {"target": "marine", "count": 1}},
            ],
            "content": "Build and train without advance.",
        },
        {
            "tool_calls": [{"id": "a2", "name": "advance", "arguments": {"seconds": 5}}],
            "content": "The earlier orders are staged, so I advance.",
        },
    ]
    seen = []

    def call_llm(messages, tools=None):
        seen.append(messages)
        return replies[len(seen) - 1]

    env = Environment(record_dir=tmp_path)
    agent = LLMAgent(call_llm, verbose=False)
    try:
        observation = env.reset(EpisodeConfig(game_time_limit_seconds=10))
        turn = env.begin_tool_turn()
        agent(AgentInput(observation, None, tuple(env.tool_specs()), turn.call))
        batch = turn.finish()
    finally:
        env.close()
    submitted = [call["name"] for call in batch.to_dicts()]
    assert submitted == ["build", "train", "advance"]
    ids = []
    for message in agent.messages:
        if message.get("role") == "assistant":
            ids.extend(call["id"] for call in message.get("tool_calls") or [])
        if message.get("role") == "tool":
            ids.remove(message["tool_call_id"])
    assert ids == []
    first_results = [message["content"] for message in seen[1] if message.get("role") == "tool"]
    assert any("map" in content.lower() for content in first_results)
    assert all("not_submitted" not in content for content in first_results)
    mixed_reply = next(
        message for message in seen[2]
        if message.get("role") == "assistant" and len(message.get("tool_calls") or []) == 3
    )
    mixed_ids = {call["id"] for call in mixed_reply["tool_calls"]}
    mixed_results = [
        message["content"] for message in seen[2]
        if message.get("role") == "tool" and message.get("tool_call_id") in mixed_ids
    ]
    assert len(mixed_results) == 3
    assert all("not_submitted" in content for content in mixed_results)


def test_advance_bundled_with_other_calls_is_not_submitted(tmp_path):
    replies = [
        {
            "tool_calls": [
                {"id": "a", "name": "advance", "arguments": {"seconds": 5}},
                {"id": "b", "name": "build", "arguments": {"target": "supply_depot"}},
            ],
            "content": "Advance and also build.",
        },
        {
            "tool_calls": [{"id": "b2", "name": "build", "arguments": {"target": "supply_depot"}}],
            "content": "Build the depot first.",
        },
        {
            "tool_calls": [{"id": "a2", "name": "advance", "arguments": {"seconds": 5}}],
            "content": "Now advance alone.",
        },
    ]
    seen = []

    def call_llm(messages, tools=None):
        seen.append(messages)
        return replies[len(seen) - 1]

    env = Environment(record_dir=tmp_path)
    agent = LLMAgent(call_llm, verbose=False)
    try:
        observation = env.reset(EpisodeConfig(game_time_limit_seconds=10))
        turn = env.begin_tool_turn()
        agent(AgentInput(observation, None, tuple(env.tool_specs()), turn.call))
        assert [call["name"] for call in turn.finish().to_dicts()] == ["build", "advance"]
    finally:
        env.close()
    first_tools = [message for message in seen[1] if message.get("role") == "tool"]
    assert len(first_tools) == 2
    assert all("not_submitted" in message["content"] for message in first_tools)


@pytest.mark.parametrize("race", ["terran", "protoss", "zerg"])
def test_short_race_record_uses_that_race_and_stays_in_one_folder(tmp_path, race):
    def call_llm(messages, tools=None):
        return {
            "tool_calls": [{"id": "a", "name": "advance", "arguments": {"seconds": 5}}],
            "content": "No new order, so I advance.",
            "model": "mock",
        }

    env = Environment(record_dir=tmp_path)
    agent = LLMAgent(call_llm, verbose=False)
    try:
        observation = env.reset(EpisodeConfig(race=race, game_time_limit_seconds=6))
        turn = env.begin_tool_turn()
        result = agent(AgentInput(observation, None, tuple(env.tool_specs()), turn.call))
        drive_result = env.step(turn.finish(), agent_context=result.agent_context)
        assert drive_result[0].game.game_time_seconds > 0
    finally:
        env.close()
    episode = read_episode(env.record_path)
    assert f"controlling {race}" in episode["messages"][0]["content"].lower()
    names = [tool["function"]["name"] for tool in episode["session"]["tools"]]
    assert names == [spec.name for spec in env.tool_specs()]
    assert {
        "episode.txt", "session.json", "metrics.json", "log.txt",
    } <= {path.name for path in env.record_path.iterdir()}
    assert episode["metrics"]["tool_calls"][-1]["name"] == "advance"
    assert episode["metrics"]["summary"]["integrity"]["ok"] is True
    assert episode["trajectory"] is None
    assert not list(tmp_path.glob("run_*.json"))
    assert not list(tmp_path.glob("interactions.jsonl"))


def test_old_session_without_trajectory_is_marked_legacy(tmp_path):
    directory = tmp_path / "old"
    directory.mkdir()
    (directory / "episode.txt").write_text(
        "SC2Bench episode\n\nConfiguration and versions\n"
        + json.dumps({
            "schema_version": "0.6",
            "episode_id": "old",
            "platform_prompt_char_count": 4,
            "config": {"race": "terran"},
        })
        + "\n\nPlatform contract\nold\n",
        encoding="utf-8",
    )
    (directory / "session.json").write_text(
        json.dumps({"episode_id": "old", "messages": [], "tools": None}),
        encoding="utf-8",
    )
    episode = read_episode(directory)
    assert episode["tool_protocol_version"] == "legacy"
    assert "model session only; no platform trajectory" in episode["record_notes"]
    assert episode["trajectory"] is None


def test_scripted_step_still_records_a_decision(tmp_path):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=10))
        drive(env, [{"name": "advance", "arguments": {"seconds": 5}}])
    finally:
        env.close()
    document = json.loads((env.record_path / "metrics.json").read_text(encoding="utf-8"))
    assert document["summary"]["decision_count"] == 1


def _integrity_for(calls):
    from sc2bench_env.recording.trajectory import TrajectoryRecorder

    recorder = TrajectoryRecorder(episode_id="episode", config={"race": "protoss"})
    recorder.steps.extend(calls)
    recorder.steps.append({"type": "end", "result": None, "replay_saved": False})
    return recorder._integrity()


def _call(name, state, turn_id="turn-1"):
    return {
        "type": "tool_call",
        "name": name,
        "turn_id": turn_id,
        "decision_index": 0,
        "execution_state": state,
        "status": "rejected" if state == "rejected" else state,
        "result": {"status": "rejected" if state == "rejected" else state},
        "arguments": {"seconds": 5} if name == "advance" else {},
    }


def test_rejected_advance_then_retry_stays_consistent():
    report = _integrity_for([
        _call("advance", "rejected"),
        _call("advance", "decision_ready"),
    ])
    assert report["ok"] is True
    assert report["errors"] == []


def test_two_successful_advances_in_one_turn_still_fail():
    report = _integrity_for([
        _call("advance", "decision_ready"),
        _call("train", "staged"),
        _call("advance", "decision_ready"),
    ])
    assert report["ok"] is False
    assert any("more than one advance" in error for error in report["errors"])
