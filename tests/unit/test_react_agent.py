"""ReAct integration checks with real tool turns and a deterministic backend."""
from copy import deepcopy
import json

import pytest

from agents.llm_agent import LLMAgent
from agents.llm_agent.cli import run_episode
from agents.react_agent import ReActAgent, create_agent
from sc2bench_env import Environment
from sc2bench_env.interface.agent import AgentInput
from sc2bench_env.interface.config import EpisodeConfig


def reply(name="advance", arguments=None, note="The current work needs time."):
    return {"content": note, "model": "mock", "tool_calls": [{
        "id": "call", "name": name,
        "arguments": arguments if arguments is not None else {"seconds": 5},
    }]}


@pytest.mark.parametrize("race,worker", [("terran", "scv"), ("protoss", "probe"), ("zerg", "drone")])
def test_feedback_loop_full_session_and_saved_analysis(tmp_path, capsys, race, worker):
    seen = []
    def call(messages, tools=None):
        seen.append(deepcopy(messages))
        if len(seen) == 1:
            return reply("query_unit_data", {"race": race, "names": [worker]}, "Check the worker cost.")
        if len(seen) == 2:
            assert seen[-1][-1]["role"] == "tool"
            return reply("train", {"target": worker, "count": 1}, "Train one worker with the available minerals.")
        return reply()

    agent = create_agent(call_llm=call)
    env = Environment("fake", record_dir=tmp_path)
    try:
        info = run_episode(env, agent, EpisodeConfig(race=race, game_time_limit_seconds=10))
        directory = env.record_path
    finally:
        env.close()
    assert info["end_reason"] == "time_limit"
    assert len(seen) == 4
    assert len([m for m in agent.messages if m["role"] == "system"]) == 1
    assert "Interact with StarCraft II" in seen[0][0]["content"]
    assert f"controlling {race}" in seen[0][0]["content"]
    assert "Train one worker" in json.dumps(seen[-1])
    assert "Train one worker" in capsys.readouterr().out
    session = json.loads((directory / "session.json").read_text(encoding="utf8"))
    assert session["scope"] == "episode"
    assert "Train one worker" in json.dumps(session["messages"])
    assert (directory / "metrics.json").is_file()
    assert "Train one worker" in (directory / "log.txt").read_text(encoding="utf8")


def test_rejected_reply_does_not_discard_earlier_staged_work(tmp_path):
    replies = iter([
        reply("train", {"target": "scv", "count": 1}),
        {"content": "Add supply and advance.", "tool_calls": [
            {"id": "advance", "name": "advance", "arguments": {"seconds": 5}},
            {"id": "build", "name": "build", "arguments": {"target": "supply_depot"}},
        ]},
        reply(),
    ])
    agent = create_agent(call_llm=lambda messages, **_: next(replies), verbose=False)
    env = Environment("fake", record_dir=tmp_path)
    try:
        obs = env.reset(EpisodeConfig())
        turn = env.begin_tool_turn()
        agent(AgentInput(obs, None, tuple(env.tool_specs()), turn.call))
        actions = turn.finish().to_dicts()
    finally:
        env.close()
    assert [a["name"] for a in actions] == ["train", "advance"]
    assert sum(m["role"] == "tool" and "not_submitted" in m["content"] for m in agent.messages) == 2


def test_api_failure_is_interruption_not_result(tmp_path):
    agent = create_agent(call_llm=lambda messages, **_: {
        "error": "TimeoutError", "retryable": False,
    }, verbose=False)
    env = Environment("fake", record_dir=tmp_path)
    try:
        info = run_episode(env, agent, EpisodeConfig())
    finally:
        env.close()
    assert info["end_reason"] == "agent_call_failed"
    assert info.get("result") is None


def test_factory_and_original_agent_are_separate():
    a = create_agent(call_llm=lambda messages, **_: reply())
    b = create_agent(call_llm=lambda messages, **_: reply())
    assert isinstance(a, ReActAgent) and a is not b
    assert a.conversation_mode == "full_session" and a.skill_name is None
    assert a.max_tool_rounds == 24
    assert LLMAgent._append_turn_input is not ReActAgent._append_turn_input


def test_cli_selects_model_without_printing_secret(tmp_path, capsys):
    from agents.react_agent.cli import main
    config = tmp_path / "models.json"
    config.write_text(json.dumps({"llm_agents_pool": {"test": {
        "model_name": "mock-model", "api_url": "http://localhost:1/v1",
        "api_key": "private-test-value", "temperature": 0.7, "is_reasoning": True,
    }}}), encoding="utf8")
    assert main(["--config", str(config), "--model-key", "test", "--no-thinking", "--dry-run"]) == 0
    text = capsys.readouterr().out
    data = json.loads(text)
    assert "private-test-value" not in text
    assert data["temperature"] == 0.7 and data["thinking"] is False
    assert data["agent"] == "agents.react_agent.ReActAgent"
    assert data["conversation_mode"] == "full_session"


@pytest.mark.parametrize("mode", ["full_session", "per_decision"])
def test_thinking_fields_survive_tool_and_text_replies(tmp_path, capsys, mode):
    seen = []
    def call(messages, tools=None):
        seen.append(deepcopy(messages))
        if len(seen) == 1:
            return {"content": "The worker is still training.", "tool_calls": [],
                    "reasoning": "Review the current production before acting."}
        result = reply()
        result["reasoning"] = "Allow the existing production to finish."
        return result
    agent = create_agent(call_llm=call, thinking_requested=True, conversation_mode=mode)
    env = Environment("fake", record_dir=tmp_path)
    try:
        info = run_episode(env, agent, EpisodeConfig(game_time_limit_seconds=10))
        directory = env.record_path
    finally:
        env.close()
    assert info["end_reason"] == "time_limit"
    first_note = next(m for m in seen[1] if m["role"] == "assistant")
    assert first_note["reasoning_content"] == "Review the current production before acting."
    assert "Allow the existing production to finish." in capsys.readouterr().out
    session = json.loads((directory / "session.json").read_text(encoding="utf8"))
    assert any(m.get("reasoning_content") == "Allow the existing production to finish."
               for m in session["messages"])
    assert "Interact with StarCraft II" in session["messages"][0]["content"]
    assert "Allow the existing production to finish." in (directory / "log.txt").read_text(encoding="utf8")


def test_factory_passes_native_thinking_setting(monkeypatch):
    import agents.react_agent.agent as module
    options = {}
    def make(**kwargs):
        options.update(kwargs)
        return lambda messages, **_: reply()
    monkeypatch.setattr(module, "make_llm_call", make)
    module.create_agent(thinking_requested=True)
    assert options["thinking"] is True


def test_cli_runs_and_records_direct_episode(tmp_path, monkeypatch):
    import agents.react_agent.cli as cli
    monkeypatch.setattr(cli, "make_llm_call", lambda **_: lambda messages, **__: reply())
    monkeypatch.setattr(cli, "Environment", lambda backend, **kwargs: Environment("fake", **kwargs))
    assert cli.main(["--record-dir", str(tmp_path), "--game-time-limit", "10", "--seed", "7"]) == 0
    paths = list(tmp_path.rglob("session.json"))
    assert len(paths) == 1
    session = json.loads(paths[0].read_text(encoding="utf8"))
    assert "Interact with StarCraft II" in session["messages"][0]["content"]
    assert (paths[0].parent / "log.txt").is_file()


@pytest.mark.parametrize("race,worker", [("terran", "scv"), ("protoss", "probe"), ("zerg", "drone")])
def test_action_query_and_final_advance_can_share_reply(tmp_path, race, worker):
    calls = [
        {"id": "train", "name": "train", "arguments": {"target": worker, "count": 1}},
        {"id": "query", "name": "query_unit_data", "arguments": {"race": race, "names": [worker]}},
        {"id": "advance", "name": "advance", "arguments": {"seconds": 5}},
    ]
    agent = create_agent(call_llm=lambda messages, **_: {"content": "Train a worker and submit this decision.", "tool_calls": calls}, verbose=False)
    env = Environment("fake", record_dir=tmp_path)
    try:
        obs = env.reset(EpisodeConfig(race=race))
        turn = env.begin_tool_turn()
        result = agent(AgentInput(obs, None, tuple(env.tool_specs()), turn.call))
        actions = turn.finish().to_dicts()
    finally:
        env.close()
    assert [a["name"] for a in actions] == ["train", "advance"]
    replies = [m for m in result.agent_context["messages"] if m["role"] == "tool"]
    assert len(replies) == 3
    assert not any("not_submitted" in m["content"] for m in replies)


def test_query_after_staged_action_remains_available(tmp_path):
    replies = iter([
        reply("train", {"target": "scv", "count": 1}),
        reply("query_map_overview", {}),
        reply(),
    ])
    seen = []
    def call(messages, tools=None):
        seen.append(deepcopy(messages))
        return next(replies)
    agent = create_agent(call_llm=call, verbose=False)
    env = Environment("fake", record_dir=tmp_path)
    try:
        info = run_episode(env, agent, EpisodeConfig(game_time_limit_seconds=5))
    finally:
        env.close()
    assert info["end_reason"] == "time_limit"
    assert len(seen) == 3 and seen[-1][-1]["role"] == "tool"
    assert "Map overview at game time 0" in seen[-1][-1]["content"]


def test_unknown_tool_execution_is_not_retried(tmp_path):
    invoked = []
    def fail(tool_call):
        invoked.append(tool_call.name)
        raise OSError("delivery failed")
    agent = create_agent(call_llm=lambda messages, **_: {"content": "Train and advance.", "tool_calls": [
        {"id": "train", "name": "train", "arguments": {"target": "scv", "count": 1}},
        {"id": "advance", "name": "advance", "arguments": {"seconds": 5}},
    ]}, verbose=False)
    env = Environment("fake", record_dir=tmp_path)
    try:
        obs = env.reset(EpisodeConfig())
        from sc2bench_env.interface.agent import AgentStopped
        with pytest.raises(AgentStopped) as stop:
            agent(AgentInput(obs, None, tuple(env.tool_specs()), fail))
    finally:
        env.close()
    assert invoked == ["train"]
    replies = [m for m in stop.value.agent_context["messages"] if m["role"] == "tool"]
    assert "execution_unknown" in replies[0]["content"]
    assert "not_submitted" in replies[1]["content"]


def test_reasoning_steps_are_acknowledged_without_calling_environment(tmp_path):
    seen, actual_calls = [], []
    replies = iter([
        {"content": "I need to inspect the available information before choosing an action.", "tool_calls": []},
        {"content": "The ongoing work can continue while I wait.", "tool_calls": []},
        reply(),
    ])
    def call(messages, tools=None):
        seen.append(deepcopy(messages))
        return next(replies)
    env = Environment("fake", record_dir=tmp_path)
    agent = create_agent(call_llm=call, verbose=False)
    try:
        obs = env.reset(EpisodeConfig())
        turn = env.begin_tool_turn()
        def tool_call(call):
            actual_calls.append(call.name)
            return turn.call(call)
        agent(AgentInput(obs, None, tuple(env.tool_specs()), tool_call))
        actions = turn.finish().to_dicts()
    finally:
        env.close()
    assert actual_calls == ["advance"]
    assert len(seen) == 3
    assert seen[1][-1] == {"role": "user", "content": "OK."}
    assert seen[2][-1] == {"role": "user", "content": "OK."}
    assert actions == [{"name": "advance", "arguments": {"seconds": 5}}]


def test_next_observation_has_no_repeated_actor_instruction(tmp_path):
    seen = []
    def call(messages, tools=None):
        seen.append(deepcopy(messages))
        return reply()
    env = Environment("fake", record_dir=tmp_path)
    agent = create_agent(call_llm=call, verbose=False)
    try:
        run_episode(env, agent, EpisodeConfig(game_time_limit_seconds=10))
    finally:
        env.close()
    assert len(seen) == 2
    assert all(m[-1]["role"] == "user" and "[Current Observation]" in m[-1]["content"] for m in seen)
    assert sum(m["role"] == "system" for m in seen[-1]) == 1
    assert not any("Select the next tools using" in (m.get("content") or "") for m in seen[-1])


@pytest.mark.parametrize('race,skill', [('terran','terran/two-base-tanks'),
    ('protoss','protoss/macro-stalkers'),('zerg','zerg/lurkers')])
def test_selected_skill_reaches_the_model_once_per_session(tmp_path,race,skill):
    from agents.llm_agent.skills import load_skill
    seen=[]
    def call(messages,tools=None):
        seen.append(deepcopy(messages))
        return reply()
    env=Environment('fake',record_dir=tmp_path)
    agent=create_agent(call_llm=call,verbose=False,skill_name=skill)
    try:
        run_episode(env,agent,EpisodeConfig(race=race,game_time_limit_seconds=10))
    finally:
        env.close()
    text=load_skill(skill)[1]
    assert len(seen)==2
    assert all(m[0]['content'].count(text)==1 for m in seen)
    assert all(sum(x['role']=='system' for x in m)==1 for m in seen)


def test_cli_rejects_a_skill_from_another_race_before_model_setup(monkeypatch):
    from agents.react_agent import cli
    monkeypatch.setattr(cli,'llm_model',lambda:pytest.fail('model setup ran before validation'))
    with pytest.raises(SystemExit) as stopped:
        cli.main(['--race','terran','--skill','zerg/lurkers','--dry-run'])
    assert stopped.value.code==2
