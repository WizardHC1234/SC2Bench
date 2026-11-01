"""Ordinary llm_agent: session history, advance, no skills."""
from tests.helpers.drive import drive
from sc2bench_env.adapters.llm import LLMAdapter
from agents.llm_agent.prompts import platform_turn_messages

import json

import pytest

from sc2bench_env import Environment, ToolResult
from sc2bench_env.benchmark import AgentInput
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.recording.reader import read_episode

import agents.llm_agent.cli as cli_module
from agents.llm_agent import (
    AVAILABLE_SKILLS, DEFAULT_API_BASE_URL, DEFAULT_API_KEY, DEFAULT_MODEL,
    DEFAULT_SKILL, LLMAgent, create_agent, load_skill, skill_race,
)
from agents.llm_agent.cli import main
from agents.llm_agent.tools import tool_schemas
from agents.llm_agent.config import llm_api_key, llm_base_url, llm_model


def _accept(_call):
    return ToolResult(
        status="decision_ready", decision_boundary=True,
        data={"action_count": 0, "advance_seconds": 5, "staged_calls": []},
    )


def _ask(env, agent, observation, feedback=None):
    tool_turn = env.begin_tool_turn()
    result = agent(AgentInput(
        observation, feedback, tuple(env.tool_specs()), tool_turn.call,
    ))
    return result, tool_turn


def _advance(seconds: int, call_id: str = "a") -> dict:
    return {
        "tool_calls": [{"id": call_id, "name": "advance", "arguments": {"seconds": seconds}}],
        "content": "Using the current observation for this call.", "raw_content": "",
    }


def test_create_agent_returns_isolated_instances():
    first = create_agent(call_llm=lambda messages, **_: _advance(5))
    second = create_agent(call_llm=lambda messages, **_: _advance(7))
    assert first is not second
    assert first.messages == second.messages == []


def test_default_is_no_skill_and_race_skill_can_be_loaded(tmp_path):
    assert DEFAULT_SKILL == "none"
    assert load_skill(DEFAULT_SKILL) is None
    loaded = load_skill("terran/two-base-tanks")
    assert loaded is not None
    assert loaded[0] == "terran/two-base-tanks"
    assert "# Two-Base Tanks" in loaded[1]
    assert "20 Siege Tanks and 100 Marines" in loaded[1]

    seen = []

    def call_llm(messages, tools=None):
        seen.append(messages[0]["content"])
        return _advance(5)

    env = Environment(record_dir=tmp_path)
    try:
        observation = env.reset(EpisodeConfig(game_time_limit_seconds=10))
        request = AgentInput(observation, None, tuple(env.tool_specs()), _accept)
        LLMAgent(call_llm, verbose=False,
                 skill_name="terran/two-base-tanks")(request)
        LLMAgent(call_llm, verbose=False)(request)
        assert "[Agent Skill: terran/two-base-tanks]" in seen[0]
        assert "[Agent Skill:" not in seen[1]
    finally:
        env.close()


def test_all_sharpy_skills_are_discovered_and_nonempty():
    assert sum(skill_race(name) == "terran" for name in AVAILABLE_SKILLS) == 10
    assert sum(skill_race(name) == "protoss" for name in AVAILABLE_SKILLS) == 11
    assert sum(skill_race(name) == "zerg" for name in AVAILABLE_SKILLS) == 11
    assert len(AVAILABLE_SKILLS) == 32
    for name in AVAILABLE_SKILLS:
        loaded = load_skill(name)
        assert loaded is not None
        canonical, text = loaded
        assert canonical == name
        assert f"race: {skill_race(name)}" in text
        assert "kind: strategy" in text
        assert len(text.splitlines()) >= 10
        assert "# " in text
        assert "## Examples" not in text
        assert "Source adaptation:" not in text
        assert "## SC2Bench adaptation" not in text


def test_module_help_runs_from_repo_root(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_single_match_cli_records_one_direct_episode_without_batch_wrapper(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        cli_module,
        "make_llm_call",
        lambda **_kwargs: (
            lambda messages, tools=None: _advance(5)
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "Environment",
        lambda _backend, record_dir=None: Environment("fake", record_dir=record_dir),
    )
    assert cli_module.main([
        "--record-dir", str(tmp_path), "--game-time-limit", "2", "--quiet",
    ]) == 0
    children = list(tmp_path.iterdir())
    assert len(children) == 1
    episode_dir = children[0]
    assert episode_dir.is_dir()
    assert not episode_dir.name.startswith("batch_")
    assert not (episode_dir / "batch.json").exists()
    assert not (episode_dir / "episodes").exists()
    episode = read_episode(episode_dir)
    assert episode["summary"]["status"] == "completed"
    assert episode["summary"]["end_reason"] == "time_limit"


def test_second_decision_keeps_first_assistant_and_new_observation(tmp_path):
    replies = [
        {"tool_calls": [
            {"id": "c1", "name": "build", "arguments": {"target": "supply_depot"}},
        ], "content": "Using the current observation for this call.", "raw_content": ""},
        {"tool_calls": [
            {"id": "c2", "name": "advance", "arguments": {"seconds": 5}},
        ], "content": "Using the current observation for this call.", "raw_content": ""},
        {"tool_calls": [
            {"id": "c3", "name": "advance", "arguments": {"seconds": 8}},
        ], "content": "Using the current observation for this call.", "raw_content": ""},
    ]
    seen = []

    def call_llm(messages, tools=None):
        seen.append([dict(item) for item in messages])
        return replies[len(seen) - 1]

    env = Environment(record_dir=tmp_path)
    agent = LLMAgent(call_llm, verbose=False)
    try:
        observation = env.reset(EpisodeConfig(game_time_limit_seconds=20))
        first, tool_turn = _ask(env, agent, observation)
        batch = tool_turn.finish()
        assert batch.to_dicts()[-1] == {"name": "advance", "arguments": {"seconds": 5}}
        observation, feedback, _, _ = env.step(batch, agent_context=first.agent_context)
        second, tool_turn = _ask(env, agent, observation, feedback)
        assert tool_turn.finish().to_dicts() == [{"name": "advance", "arguments": {"seconds": 8}}]
        assert seen[0][0]["role"] == "system"
        assert seen[2][0] == seen[0][0]
        assert seen[2][1] == seen[0][1]
        assert seen[2][2]["role"] == "assistant"
        assert seen[2][2].get("tool_calls")
        assert seen[2][-1]["role"] == "user"
        assert "[Current Observation]" in seen[2][-1]["content"]
        assert seen[2][-1] != seen[0][1]
    finally:
        env.close()


def test_two_agent_histories_do_not_share_messages():
    def call_llm(messages, tools=None):
        return _advance(5)

    first, second = LLMAgent(call_llm, verbose=False), LLMAgent(call_llm, verbose=False)
    env = Environment(record_trajectory=False)
    try:
        observation = env.reset(EpisodeConfig(game_time_limit_seconds=1))
        request = AgentInput(observation, None, tuple(env.tool_specs()), _accept)
        first(request)
        assert second.messages == []
        assert first.messages[-1]["role"] == "tool"
    finally:
        env.close()


def test_tool_loop_queries_then_submits_advance():
    calls = []

    def call_llm(messages, tools=None):
        calls.append({"n": len(messages), "tools": bool(tools)})
        if len(calls) == 1:
            return {
                "tool_calls": [{
                    "id": "c1", "name": "query_unit_data",
                    "arguments": {"race": "terran", "names": ["siege_tank"]},
                }],
                "content": "Using the current observation for this call.", "raw_content": "",
            }
        sequence = [
            {"id": "c2", "name": "build", "arguments": {"target": "factory_techlab"}},
            {"id": "c3", "name": "train", "arguments": {"target": "siege_tank", "count": 2}},
            {"id": "c4", "name": "advance", "arguments": {"seconds": 20}},
        ]
        call = sequence[len(calls) - 2]
        return {
            "tool_calls": [call],
            "content": "Using the current observation for this call.", "raw_content": "",
        }

    env = Environment(record_trajectory=False)
    agent = LLMAgent(call_llm, verbose=False)
    try:
        observation = env.reset(EpisodeConfig(game_time_limit_seconds=30))
        turn, tool_turn = _ask(env, agent, observation)
        batch = tool_turn.finish()
        assert batch.to_dicts() == [
            {"name": "build", "arguments": {"target": "factory_techlab"}},
            {"name": "train", "arguments": {"target": "siege_tank", "count": 2}},
            {"name": "advance", "arguments": {"seconds": 20}},
        ]
        assert calls[0]["tools"] and calls[1]["tools"]
        assert turn.agent_context["tools"] == tool_schemas(tuple(env.tool_specs()))
        tool_messages = [message for message in agent.messages if message.get("role") == "tool"]
        assert any("siege_tank" in message["content"] for message in tool_messages)
        assert env.backend.snapshot().game_time_seconds == 0
        observation, _, _, _ = env.step(batch)
        assert observation.game.game_time_seconds == 20
    finally:
        env.close()


def test_actions_can_be_staged_across_rounds():
    replies = [
        {"tool_calls": [
            {"id": "q1", "name": "query_unit_data", "arguments": {"race": "terran", "names": ["marine"]}},
        ], "content": "Using the current observation for this call.", "raw_content": ""},
        {"tool_calls": [
            {"id": "a1", "name": "build", "arguments": {"target": "supply_depot"}},
        ], "content": "Using the current observation for this call.", "raw_content": ""},
        {"tool_calls": [
            {"id": "a2", "name": "advance", "arguments": {"seconds": 12}},
        ], "content": "Using the current observation for this call.", "raw_content": ""},
    ]
    seen = []

    def call_llm(messages, tools=None):
        seen.append(len(messages))
        return replies[len(seen) - 1]

    env = Environment(record_trajectory=False)
    agent = LLMAgent(call_llm, verbose=False)
    try:
        observation = env.reset(EpisodeConfig(game_time_limit_seconds=30))
        _turn, tool_turn = _ask(env, agent, observation)
        assert tool_turn.finish().to_dicts() == [
            {"name": "build", "arguments": {"target": "supply_depot"}},
            {"name": "advance", "arguments": {"seconds": 12}},
        ]
        assert len(seen) == 3
        assert not any(
            message.get("role") == "tool" and "not_submitted" in message["content"]
            for message in agent.messages
        )
        assert any("marine" in message["content"] for message in agent.messages if message.get("role") == "tool")
        assert any(
            "unfinished after staging 1" in message["content"]
            for message in agent.messages if message.get("role") == "tool"
        )
        assert not any(
            "pending_action_count" in message["content"]
            for message in agent.messages if message.get("role") == "tool"
        )
    finally:
        env.close()


def test_per_decision_conversation_keeps_tool_rounds_but_drops_prior_decisions():
    seen = []

    def call_llm(messages, tools=None):
        seen.append([dict(message) for message in messages])
        return {
            "tool_calls": [{"id": f"a{len(seen)}", "name": "advance", "arguments": {"seconds": 5}}],
            "content": "The current state needs no additional action.",
            "raw_content": "",
        }

    env = Environment(record_trajectory=False)
    agent = LLMAgent(
        call_llm, verbose=False, skill_name="none",
        conversation_mode="per_decision",
    )
    try:
        observation = env.reset(EpisodeConfig(game_time_limit_seconds=20))
        _turn, first_tool_turn = _ask(env, agent, observation)
        observation, feedback, _, _ = env.step(first_tool_turn.finish())
        _turn, second_tool_turn = _ask(env, agent, observation, feedback)
        second_tool_turn.finish()
        assert len(seen) == 2
        assert [message["role"] for message in seen[0]] == ["system", "user"]
        assert [message["role"] for message in seen[1]] == ["system", "user"]
        assert "Time 00:05/" in seen[1][1]["content"]
    finally:
        env.close()


def test_tool_call_without_a_note_is_valid():
    replies = [
        {"tool_calls": [{"id": "q1", "name": "query_map_overview", "arguments": {}}],
         "content": "", "raw_content": ""},
        {"tool_calls": [{"id": "a1", "name": "advance", "arguments": {"seconds": 5}}],
         "content": "Minerals are still zero, so I only advance.", "raw_content": ""},
    ]
    seen = []

    def call_llm(messages, tools=None):
        seen.append(messages[0]["content"])
        return replies[len(seen) - 1]

    env = Environment(record_trajectory=False)
    agent = LLMAgent(call_llm, verbose=False)
    try:
        observation = env.reset(EpisodeConfig(game_time_limit_seconds=10))
        platform_prompt = LLMAdapter(race=env.config.race if env.config else "terran").system_prompt()
        _turn, tool_turn = _ask(env, agent, observation)
        assert tool_turn.finish().to_dicts() == [{"name": "advance", "arguments": {"seconds": 5}}]
        assert "Before any reply that calls tools" not in seen[0]
        assert "Before any reply that calls tools" not in platform_prompt
        rejected = [
            message for message in agent.messages
            if message.get("role") == "tool" and "without a content note" in message["content"]
        ]
        assert rejected == []
        assert any("Map overview" in (message.get("content") or "") for message in agent.messages)
    finally:
        env.close()


def test_provider_reasoning_is_not_added_to_messages_or_console(capsys):
    def call_llm(messages, tools=None):
        return {
            "tool_calls": [
                {"id": "a", "name": "advance", "arguments": {"seconds": 5}},
            ],
            "content": "",
            "raw_content": "<think>private reasoning</think>",
            "reasoning": "private reasoning",
        }

    env = Environment(record_trajectory=False)
    agent = LLMAgent(call_llm, verbose=True)
    try:
        observation = env.reset(EpisodeConfig(game_time_limit_seconds=10))
        turn, tool_turn = _ask(env, agent, observation)
        tool_turn.finish()
        assert "private reasoning" not in json.dumps(turn.agent_context["messages"])
        assert "private reasoning" not in capsys.readouterr().out
    finally:
        env.close()


def test_tool_round_limit_must_be_positive():
    with pytest.raises(ValueError, match="tool-round"):
        LLMAgent(lambda messages, tools=None: _advance(5), max_tool_rounds=0)


def test_verbose_prints_knowledge_query_and_result(capsys):
    def call_llm(messages, tools=None):
        if not any(message.get("role") == "tool" for message in messages):
            return {
                "tool_calls": [{
                    "id": "c1", "name": "query_unit_data",
                    "arguments": {"race": "terran", "names": ["siege_tank"]},
                }],
                "content": "Using the current observation for this call.", "raw_content": "",
            }
        return {
            "tool_calls": [
                {"id": "c2", "name": "advance", "arguments": {"seconds": 5}},
            ],
            "content": "Using the current observation for this call.", "raw_content": "",
        }

    env = Environment(record_trajectory=False)
    agent = LLMAgent(call_llm, verbose=True)
    try:
        observation = env.reset(EpisodeConfig(game_time_limit_seconds=10))
        _ask(env, agent, observation)
    finally:
        env.close()
    output = capsys.readouterr().out
    assert "[Knowledge query]" in output
    assert "query_unit_data" in output
    assert "siege_tank" in output
    assert "[Tool result]" in output
    assert "decision_ready=True" in output


def test_failed_tool_round_does_not_submit_buffered_actions():
    def call_llm(messages, tools=None):
        return {"error": "TimeoutError", "retryable": False}

    env = Environment(record_trajectory=False)
    agent = LLMAgent(call_llm, verbose=False, max_api_attempts=1)
    try:
        observation = env.reset(EpisodeConfig(game_time_limit_seconds=10))
        turn, _tool_turn = _ask(env, agent, observation)
        assert turn.stop_after_call_failures
        assert env.task_manager.active_demands() == []
    finally:
        env.close()


def test_llm_defaults_match_commander_deepseek_v4_flash(monkeypatch, capsys):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert DEFAULT_API_KEY == "EMPTY"
    assert DEFAULT_API_BASE_URL.startswith(("http://", "https://"))
    assert isinstance(DEFAULT_MODEL, str) and DEFAULT_MODEL.strip()
    assert llm_api_key() == DEFAULT_API_KEY
    assert llm_base_url() == DEFAULT_API_BASE_URL
    assert llm_model() == DEFAULT_MODEL
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://override.example/v1")
    monkeypatch.setenv("LLM_MODEL", "other-model")
    assert llm_api_key() == "env-key"
    assert llm_base_url() == "https://override.example/v1"
    assert llm_model() == "other-model"
    monkeypatch.delenv("LLM_API_KEY")
    monkeypatch.delenv("LLM_BASE_URL")
    monkeypatch.delenv("LLM_MODEL")
    assert main(["--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["episode"]["race"] == "terran"
    assert preview["model"] == DEFAULT_MODEL
    assert preview["base_url"] == DEFAULT_API_BASE_URL
    assert DEFAULT_API_KEY not in json.dumps(preview)


def test_cli_accepts_matching_race_skill_and_rejects_mismatch(capsys):
    assert main([
        "--dry-run", "--race", "protoss", "--skill",
        "protoss/macro-stalkers", "--opponent", "mediumhard",
    ]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["episode"]["race"] == "protoss"
    assert preview["episode"]["opponent"] == "mediumhard"
    assert preview["skill"] == "protoss/macro-stalkers"
    with pytest.raises(SystemExit):
        main([
            "--dry-run", "--race", "protoss", "--skill", "terran/bio",
        ])


def test_query_rounds_then_one_action_batch_then_advance(tmp_path):
    replies = [
        {"tool_calls": [
            {"id": "q1", "name": "query_unit_data", "arguments": {"race": "terran", "names": ["marine"]}},
            {"id": "q2", "name": "query_building_data", "arguments": {"race": "terran", "names": ["barracks"]}},
        ], "content": "Marine and barracks facts are both missing.", "raw_content": ""},
        {"tool_calls": [
            {"id": "q3", "name": "query_route", "arguments": {"from_zone": "zone_0", "to_zone": "zone_1"}},
        ], "content": "The route depends on the unit facts.", "raw_content": ""},
        {"tool_calls": [
            {"id": "b", "name": "build", "arguments": {"target": "barracks"}},
            {"id": "t", "name": "train", "arguments": {"target": "marine", "count": 2}},
        ], "content": "Build the barracks and train marines together.", "raw_content": ""},
        {"tool_calls": [
            {"id": "a", "name": "advance", "arguments": {"seconds": 15}},
        ], "content": "Both orders are staged, so I advance.", "raw_content": ""},
    ]
    seen = []

    def call_llm(messages, tools=None):
        seen.append(messages)
        return replies[len(seen) - 1]

    env = Environment(record_trajectory=False)
    agent = LLMAgent(call_llm, verbose=False)
    try:
        observation = env.reset(EpisodeConfig(game_time_limit_seconds=30))
        _turn, tool_turn = _ask(env, agent, observation)
        assert tool_turn.finish().to_dicts() == [
            {"name": "build", "arguments": {"target": "barracks"}},
            {"name": "train", "arguments": {"target": "marine", "count": 2}},
            {"name": "advance", "arguments": {"seconds": 15}},
        ]
    finally:
        env.close()
    assert len(seen) == 4
    second_tools = [message for message in seen[1] if message.get("role") == "tool"]
    assert len(second_tools) == 2
    assert all("not_submitted" not in message["content"] for message in second_tools)
    action_message = next(
        message for message in agent.messages
        if message.get("role") == "assistant" and any(
            call["function"]["name"] == "build" for call in message.get("tool_calls") or []
        )
    )
    assert [call["function"]["name"] for call in action_message["tool_calls"]] == ["build", "train"]
    advance_message = next(
        message for message in agent.messages
        if message.get("role") == "assistant" and any(
            call["function"]["name"] == "advance" for call in message.get("tool_calls") or []
        )
    )
    assert len(advance_message["tool_calls"]) == 1
    system_prompt = seen[0][0]["content"]
    assert system_prompt.count("Example Agent decision process:") == 1
    assert "Query missing static or map facts first" in system_prompt
    assert "cancel matching unstarted work by target action" in system_prompt
    assert "describing an action or a future plan does not execute it" in system_prompt
    assert "Queries, actions and advance may be interleaved" not in system_prompt
    for heading in (
        "1. Role and objective",
        "2. Game basics",
        "3. Reading Observation",
        "4. Tool interaction",
        "5. Platform execution model",
        "6. Decision guidance",
    ):
        assert system_prompt.count(heading) == 1


def test_mixed_reply_is_not_submitted_and_keeps_every_call_id():
    replies = [
        {"tool_calls": [
            {"id": "q", "name": "query_map_overview", "arguments": {}},
            {"id": "b", "name": "build", "arguments": {"target": "supply_depot"}},
            {"id": "a", "name": "advance", "arguments": {"seconds": 5}},
        ], "content": "Query, build, and advance together.", "raw_content": ""},
        {"tool_calls": [
            {"id": "b2", "name": "build", "arguments": {"target": "supply_depot"}},
            {"id": "a2", "name": "advance", "arguments": {"seconds": 5}},
        ], "content": "Build and advance together.", "raw_content": ""},
        {"tool_calls": [
            {"id": "b3", "name": "build", "arguments": {"target": "supply_depot"}},
        ], "content": "Build the depot by itself.", "raw_content": ""},
        {"tool_calls": [
            {"id": "q2", "name": "query_map_overview", "arguments": {}},
        ], "content": "I still want the map.", "raw_content": ""},
        {"content": "I will just describe the next step.", "raw_content": ""},
        {"tool_calls": [
            {"id": "a3", "name": "advance", "arguments": {"seconds": 5}},
        ], "content": "The depot is staged, so I advance.", "raw_content": ""},
    ]
    seen = []

    def call_llm(messages, tools=None):
        seen.append(messages)
        return replies[len(seen) - 1]

    env = Environment(record_trajectory=False)
    agent = LLMAgent(call_llm, verbose=False)
    try:
        observation = env.reset(EpisodeConfig(game_time_limit_seconds=20))
        _turn, tool_turn = _ask(env, agent, observation)
        assert [call["name"] for call in tool_turn.finish().to_dicts()] == [
            "build", "advance",
        ]
    finally:
        env.close()
    ids = []
    for message in agent.messages:
        if message.get("role") == "assistant":
            ids.extend(call["id"] for call in message.get("tool_calls") or [])
        if message.get("role") == "tool":
            ids.remove(message["tool_call_id"])
    assert ids == []
    rejected = [
        message["content"] for message in agent.messages
        if message.get("role") == "tool" and "not_submitted" in message["content"]
    ]
    assert len(rejected) == 6
    assert any("mixes stages" in content for content in rejected)
    assert any("left the query stage" in content for content in rejected)
    assert any("already staged" in message.get("content", "") for message in seen[-1] if message.get("role") == "user")


def test_race_ability_alone_is_an_action(tmp_path):
    replies = [
        {"tool_calls": [
            {"id": "i", "name": "inject_larva", "arguments": {}},
        ], "content": "A Queen is ready, so I inject.", "raw_content": ""},
        {"tool_calls": [
            {"id": "a", "name": "advance", "arguments": {"seconds": 5}},
        ], "content": "The inject is staged, so I advance.", "raw_content": ""},
    ]
    seen = []

    def call_llm(messages, tools=None):
        seen.append(messages)
        return replies[len(seen) - 1]

    env = Environment(record_trajectory=False)
    agent = LLMAgent(call_llm, verbose=False)
    try:
        observation = env.reset(EpisodeConfig(race="zerg", game_time_limit_seconds=20))
        _turn, tool_turn = _ask(env, agent, observation)
        assert tool_turn.finish().to_dicts() == [
            {"name": "inject_larva", "arguments": {}},
            {"name": "advance", "arguments": {"seconds": 5}},
        ]
    finally:
        env.close()
    assert not any("not_submitted" in message.get("content", "") for message in agent.messages)
    assert not any("mixes stages" in message.get("content", "") for message in seen[1])


def test_agent_rejects_skill_for_another_race(tmp_path):
    env = Environment(record_dir=tmp_path)
    try:
        observation = env.reset(EpisodeConfig(
            race="terran", game_time_limit_seconds=10,
        ))
        request = AgentInput(observation, None, tuple(env.tool_specs()), _accept)
        agent = LLMAgent(
            lambda messages, **_: _advance(5), verbose=False,
            skill_name="zerg/roach-rush",
        )
        with pytest.raises(ValueError, match="requires race 'zerg'"):
            agent(request)
    finally:
        env.close()
