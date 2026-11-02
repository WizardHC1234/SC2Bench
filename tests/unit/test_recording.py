"""Durable per-episode records without running StarCraft II."""
from tests.helpers.drive import drive
from sc2bench_env.adapters.llm import LLMAdapter
from agents.llm_agent.prompts import platform_turn_messages

import json

import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.recording.reader import read_episode


def test_atomic_record_retries_transient_windows_lock(tmp_path, monkeypatch):
    from pathlib import Path
    from sc2bench_env.recording.trajectory import TrajectoryRecorder
    original = Path.replace
    attempts = []

    def locked_once(source, target):
        attempts.append(target)
        if len(attempts) == 1:
            error = PermissionError("temporary sharing lock")
            error.winerror = 32
            raise error
        return original(source, target)

    monkeypatch.setattr(Path, "replace", locked_once)
    monkeypatch.setattr("sc2bench_env.recording.trajectory.time.sleep", lambda _: None)
    target = tmp_path / "record.json"
    TrajectoryRecorder._write_atomic(target, {"ready": True})
    assert json.loads(target.read_text()) == {"ready": True}
    assert len(attempts) == 2


def test_atomic_record_surfaces_persistent_lock_preserving_old_json(tmp_path, monkeypatch):
    from pathlib import Path
    from sc2bench_env.recording.trajectory import TrajectoryRecorder
    target = tmp_path / "record.json"
    TrajectoryRecorder._write_atomic(target, {"old": True})
    attempts = []

    def always_locked(source, destination):
        attempts.append(destination)
        error = PermissionError("persistent sharing lock")
        error.winerror = 5
        raise error

    monkeypatch.setattr(Path, "replace", always_locked)
    monkeypatch.setattr("sc2bench_env.recording.trajectory.time.sleep", lambda _: None)
    with pytest.raises(PermissionError):
        TrajectoryRecorder._write_atomic(target, {"new": True})
    assert len(attempts) == 6
    assert json.loads(target.read_text()) == {"old": True}


def test_atomic_record_does_not_retry_other_permission_errors(tmp_path, monkeypatch):
    from pathlib import Path
    from sc2bench_env.recording.trajectory import TrajectoryRecorder
    attempts = []

    def denied(source, destination):
        attempts.append(destination)
        raise PermissionError("not a Windows sharing error")

    monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(PermissionError):
        TrajectoryRecorder._write_atomic(tmp_path / "record.json", {})
    assert len(attempts) == 1

def _steps(env):
    return env.trajectory()["steps"]


def test_episode_is_saved_before_close_and_inputs_are_frozen(tmp_path):
    env = Environment(record_dir=tmp_path)
    initial = env.reset()
    directory = env.record_path
    assert directory is not None
    assert read_episode(directory)["metadata"]["config"]["map_name"] == "KairosJunctionLE"
    assert read_episode(directory)["platform_prompt"] == LLMAdapter(race=env.config.race if env.config else "terran").system_prompt()
    assert {path.name for path in directory.iterdir()} == {"episode.txt", "session.json", "metrics.json", "log.txt", "evaluation.jsonl.gz"}
    assert _steps(env)[0]["observation"] == initial.to_dict()
    assert "info" not in _steps(env)[0]["observation"]
    assert "units" not in _steps(env)[0]["observation"]
    assert read_episode(directory)["summary"] is None

    decision = [{"name": "advance", "arguments": {'seconds': 5}}]
    obs, feedback, terminated, info = drive(env, decision)
    line = _steps(env)[-1]
    assert line["submitted_decision"] == decision
    assert line["parsed_decision"][-1]["name"] == "advance"
    assert line["observation"] == obs.to_dict()
    assert line["feedback"] == feedback.to_dict()
    assert line["game_time_after_seconds"] > line["game_time_before_seconds"]
    assert line["wall_time_seconds"] >= 0
    assert line["observation_char_count"] == len(json.dumps(obs.to_dict(), ensure_ascii=False, separators=(",", ":")))
    decision[0]["name"] = "mutated"
    assert env.trajectory()["steps"][-1]["submitted_decision"][0]["name"] == "advance"
    env.close()
    assert read_episode(directory)["summary"]["status"] == "interrupted"


def test_episode_log_copies_terminal_output_until_close(tmp_path, capsys):
    import sc2.main

    env = Environment(record_dir=tmp_path)
    try:
        env.reset()
        print("episode-console-line", flush=True)
        sc2.main.logger.info("episode-loguru-line")
        text = (env.record_path / "log.txt").read_text(encoding="utf-8")
        assert "episode-console-line" in text
        # Loguru sink wiring can vary by python-sc2 install; console capture is required.
        if "episode-loguru-line" not in text:
            sc2.main.logger.warning("episode loguru sink not attached in this environment")
        assert "\x1b[" not in text
    finally:
        env.close()
    print("after-episode-console", flush=True)
    text = (env.record_path / "log.txt").read_text(encoding="utf-8")
    assert "episode-console-line" in text
    assert "after-episode-console" not in text
    captured = capsys.readouterr().out
    assert "episode-console-line" in captured
    assert "after-episode-console" in captured


def test_rejections_are_saved_without_advancing_game(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    decision = [{"name": "build", "arguments": {'target': "barracks"}}]  # no trailing wait
    drive(env, decision)
    entry = _steps(env)[-1]
    assert not entry["validation"]["accepted"]
    assert entry["validation"]["error"]
    assert entry["submitted_decision"] == decision
    assert entry["parsed_decision"] == []
    assert entry["game_time_before_seconds"] == entry["game_time_after_seconds"] == 0
    assert not env.task_manager.active_demands()
    env.close()
    summary = read_episode(env.record_path)["summary"]
    assert summary["decision_count"] == summary["rejected_count"] == 1


def test_normal_end_is_not_overwritten_by_close(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset(EpisodeConfig(game_time_limit_seconds=1))
    _, _, terminated, _ = drive(env, [{"name": "advance", "arguments": {'seconds': 5}}])
    assert terminated
    directory = env.record_path
    summary = read_episode(directory)["summary"]
    assert summary["status"] == "completed"
    assert summary["end_reason"] == "time_limit"
    assert summary["result"] == "Result.Tie"
    env.close()
    env.close()
    assert read_episode(directory)["summary"] == summary
    assert len([entry for entry in _steps(env) if entry["type"] == "end"]) == 1


def test_reset_creates_new_directory_and_finalizes_previous(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    first = env.record_path
    env.reset()
    second = env.record_path
    assert first != second
    assert read_episode(first)["summary"]["status"] == "interrupted"
    assert len(_steps(env)) == 1
    env.close()


def test_recording_can_be_disabled(tmp_path):
    directory = tmp_path / "disabled"
    env = Environment(record_trajectory=False, record_dir=directory)
    env.reset()
    drive(env, [{"name": "advance", "arguments": {'seconds': 5}}])
    env.close()
    assert env.record_path is None
    assert env.trajectory() is None
    assert not directory.exists()


class BrokenBackend(FakeBackend):
    def run_until(self, trigger):
        raise RuntimeError("backend exploded")


def test_backend_exception_preserves_prior_records_and_failure(tmp_path):
    env = Environment(BrokenBackend(), record_dir=tmp_path)
    env.reset()
    with pytest.raises(RuntimeError, match="backend exploded"):
        drive(env, [{"name": "advance", "arguments": {'seconds': 5}}])
    entries = _steps(env)
    assert entries[0]["type"] == "reset"
    assert entries[-2]["type"] == "error"
    assert entries[-2]["submitted_decision"] == [{"name": "advance", "arguments": {'seconds': 5}}]
    summary = read_episode(env.record_path)["summary"]
    assert summary["status"] == "failed"
    assert summary["result"] is None
    assert summary["decision_count"] == 1
    env.close()
    assert read_episode(env.record_path)["summary"] == summary


def test_startup_exception_is_recorded(tmp_path):
    class StartupFailure(FakeBackend):
        def start_episode(self, config):
            raise RuntimeError("startup failed")

    env = Environment(StartupFailure(), record_dir=tmp_path)
    with pytest.raises(RuntimeError, match="startup failed"):
        env.reset()
    assert read_episode(env.record_path)["summary"]["end_reason"] == "reset_error"
    assert _steps(env)[0]["type"] == "error"


def test_optional_replay_target_is_passed_to_backend(tmp_path):
    class ReplayBackend(FakeBackend):
        def set_replay_path(self, path):
            self.replay_path = path

    backend = ReplayBackend()
    env = Environment(backend, record_dir=tmp_path)
    env.reset()
    assert backend.replay_path == env.record_path / "replay.SC2Replay"
    assert "record_path" not in env.trajectory()["steps"][0]["observation"]
    env.close()


def test_rejected_non_json_values_do_not_break_recording(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    drive(env, [{"name": "train", "arguments": {'target': "marine", 'count': float("nan")}}, {"name": "advance", "arguments": {'seconds': 5}}])
    assert _steps(env)[-1]["submitted_decision"][0]["arguments"]["count"] == {"non_json_number": "nan"}
    env.close()


def test_keyboard_interrupt_is_not_a_game_defeat(tmp_path):
    class InterruptedBackend(FakeBackend):
        def run_until(self, trigger):
            raise KeyboardInterrupt()

    env = Environment(InterruptedBackend(), record_dir=tmp_path)
    env.reset()
    with pytest.raises(KeyboardInterrupt):
        drive(env, [{"name": "advance", "arguments": {'seconds': 5}}])
    summary = read_episode(env.record_path)["summary"]
    assert summary["status"] == "interrupted"
    assert summary["result"] is None


def test_close_failure_is_saved_and_reraised(tmp_path):
    class CloseFailure(FakeBackend):
        def close_episode(self):
            raise RuntimeError("cleanup failed")

    env = Environment(CloseFailure(), record_dir=tmp_path)
    env.reset()
    with pytest.raises(RuntimeError, match="cleanup failed"):
        env.close()
    assert read_episode(env.record_path)["summary"]["end_reason"] == "close_error"
    env.close()


def test_retry_ids_are_not_added_to_parsed_model_decision(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    decision = [{"name": "build", "arguments": {'target': "supply_depot"}}, {"name": "advance", "arguments": {'seconds': 5}}]
    drive(env, decision, retry_ids=["transport-1"])
    entry = _steps(env)[-1]
    assert entry["submitted_decision"] == decision
    assert all("action_id" not in action for action in entry["parsed_decision"])
    env.close()


def test_null_input_is_not_an_empty_decision(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    try:
        env.step(None)
    except TypeError as error:
        assert "DecisionBatch" in str(error)
    else:
        raise AssertionError("a live game must not accept a missing decision")
    assert all(item.get("type") != "step" for item in _steps(env))
    env.close()


def test_text_context_uses_pre_decision_observation_and_full_sections(tmp_path):
    env = Environment(record_dir=tmp_path)
    initial = env.reset()
    messages = platform_turn_messages(env.latest_observation, env.latest_feedback, env.config.race if env.config else "terran")
    for section in ("[Relevant Zones]", "[Army]", "[Abilities]", "[Economy]"):
        assert section in messages[1]["content"]
    assert "[Decision]" not in messages[1]["content"]
    assert "zone_0" in messages[1]["content"]
    drive(env, [{"name": "advance", "arguments": {'seconds': 5}}])
    entry = _steps(env)[-1]
    for key in ("messages", "context_source", "text_observation", "returned_text_observation", "agent_context"):
        assert key not in entry
    session = read_episode(env.record_path)["session"]
    assert session["messages"] == []
    assert session["tools"] is None
    assert session["model"] is None and "result" not in session
    assert not (env.record_path / "context.txt").exists()
    assert not (env.record_path / "interactions.jsonl").exists()
    env.close()


def test_session_keeps_the_tool_schema_sent_to_the_model(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    tools = [{"type": "function", "function": {"name": "advance", "description": "Advance the game."}}]
    drive(env, [{"name": "advance", "arguments": {'seconds': 5}}], agent_context={
        "messages": [{"role": "user", "content": "obs"}],
        "tools": tools,
    })
    assert read_episode(env.record_path)["session"]["tools"] == tools
    drive(env, [{"name": "advance", "arguments": {'seconds': 5}}], agent_context={
        "messages": [{"role": "user", "content": "next"}],
    })
    session = read_episode(env.record_path)["session"]
    assert session["messages"] == [{"role": "user", "content": "next"}]
    assert session["tools"] == tools
    env.close()


def test_actual_agent_context_and_transcript_are_preserved(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    agent_context = {
        "messages": [{"role": "system", "content": "custom skill"}, {"role": "user", "content": "实际输入"}],
        "messages_transcript": [{"role": "user", "content": "实际输入"}, {"role": "assistant", "content": "原始回复"}],
        "assistant_content": "原始回复",
        "text_observation": "Agent 使用的 Obs 文本",
        "usage": {"input_tokens": 123, "output_tokens": 9},
    }
    drive(env, [{"name": "advance", "arguments": {'seconds': 5}}], agent_context=agent_context)
    entry = _steps(env)[-1]
    session = read_episode(env.record_path)["session"]
    assert session["messages"] == agent_context["messages"]
    assert "usage" not in session and "assistant_content" not in session
    agent_context["messages"][0]["content"] = "changed"
    assert read_episode(env.record_path)["messages"][0]["content"] == "custom skill"
    assert read_episode(env.record_path)["messages"][1]["content"] == "实际输入"
    assert entry["submitted_decision"] == [{"name": "advance", "arguments": {'seconds': 5}}]
    assert "agent_context" not in entry
    env.close()


def test_session_keeps_the_latest_full_messages_once(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    system = "Agent harness instructions and skills" * 100
    last = None
    for index in range(3):
        last = [{"role": "system", "content": system},
                {"role": "user", "content": f"round {index}"}]
        drive(env, [{"name": "advance", "arguments": {'seconds': 5}}], agent_context={
            "messages": last, "assistant_content": '[{"name": "advance", "arguments": {"seconds": 5}}]',
            "model": "mock",
        })
    directory = env.record_path
    env.close()
    assert {path.name for path in directory.iterdir()} == {"episode.txt", "session.json", "metrics.json", "log.txt", "evaluation.jsonl.gz"}
    assert not (directory / "interactions.jsonl").exists()
    assert not (directory / f"{directory.name}.json").exists()
    record = read_episode(directory)
    assert record["messages"] == last
    assert record["session"]["model"] == "mock"
    assert "result" not in record["session"]
    assert record["messages"][0]["content"] == system
    assert "content_ref" not in json.dumps(record["session"])
    assert record["summary"]["decision_count"] == 3


def test_decision_scoped_sessions_are_saved_one_per_decision(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    for index in range(2):
        messages = [
            {"role": "system", "content": "platform"},
            {"role": "user", "content": f"observation {index}"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"advance-{index}", "type": "function",
                "function": {"name": "advance", "arguments": '{"seconds":5}'},
            }]},
            {"role": "tool", "tool_call_id": f"advance-{index}",
             "content": "status: decision_ready"},
        ]
        drive(env, [{"name": "advance", "arguments": {"seconds": 5}}], agent_context={
            "session_scope": "decision",
            "messages": messages,
            "tools": [{"type": "function", "function": {"name": "advance"}}],
            "model": "mock",
        })
    directory = env.record_path
    env.close()

    restored = read_episode(directory)
    assert restored["session"]["scope"] == "decision"
    assert restored["session"]["decision_index"] == 2
    assert [item["decision_index"] for item in restored["decision_sessions"]] == [1, 2]
    assert restored["decision_sessions"][0]["messages"][1]["content"] == "observation 0"
    assert restored["decision_sessions"][1]["messages"][-1]["content"] == "status: decision_ready"


def test_session_replaces_messages_instead_of_stacking_turns(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    for system in ("first", "second", "first"):
        drive(env, [{"name": "advance", "arguments": {'seconds': 5}}], agent_context={
            "messages": [{"role": "system", "content": system}], "assistant_content": "ok",
        })
    directory = env.record_path
    env.close()
    assert read_episode(directory)["messages"] == [{"role": "system", "content": "first"}]


def test_session_stores_supplied_messages_in_full(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    messages = platform_turn_messages(env.latest_observation, env.latest_feedback, env.config.race if env.config else "terran")
    drive(env, [{"name": "advance", "arguments": {'seconds': 5}}], agent_context={
        "messages": messages,
        "messages_transcript": [*messages, {"role": "assistant", "content": "ok"}],
        "assistant_content": "ok",
    })
    directory = env.record_path
    env.close()
    restored = read_episode(directory)
    assert restored["messages"] == messages
    assert restored["platform_prompt"] == messages[0]["content"]
    assert "content_ref" not in json.dumps(restored["session"])


def test_session_is_updated_each_round_and_result_is_written_on_close(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    assert read_episode(env.record_path)["messages"] == []
    latest = None
    for index in range(2):
        latest = platform_turn_messages(env.latest_observation, env.latest_feedback, env.config.race if env.config else "terran")
        drive(env, [{"name": "advance", "arguments": {'seconds': 5}}], agent_context={
            "messages": latest, "assistant_content": f"response {index}",
        })
        assert read_episode(env.record_path)["messages"] == latest
    env.close()
    saved = read_episode(env.record_path)
    assert saved["messages"] == latest
    assert "result" not in saved["session"]
    assert saved["summary"]["status"] == "interrupted"
    assert _steps(env)[-1]["type"] == "end"


def test_rejected_action_keeps_text_context(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    messages = platform_turn_messages(env.latest_observation, env.latest_feedback, env.config.race if env.config else "terran")
    drive(env, [{"name": "oops", "arguments": {}}], agent_context={"messages": messages, "assistant_content": "bad action"})
    entry = _steps(env)[-1]
    assert read_episode(env.record_path)["messages"] == messages
    assert entry["submitted_decision"] == [{"name": "oops", "arguments": {}}]
    assert not entry["validation"]["accepted"]
    assert "Previous Feedback" in platform_turn_messages(env.latest_observation, env.latest_feedback, env.config.race if env.config else "terran")[1]["content"]
    env.close()


def test_recording_does_not_automatically_build_reference_context(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    drive(env, [{"name": "advance", "arguments": {'seconds': 5}}])
    assert not hasattr(env, "get_context")
    env.close()


def test_exception_keeps_real_agent_input_output_outside_trajectory(tmp_path):
    env = Environment(BrokenBackend(), record_dir=tmp_path)
    env.reset()
    context = {"messages": [{"role": "user", "content": "real input"}], "assistant_content": "real output"}
    with pytest.raises(RuntimeError, match="backend exploded"):
        drive(env, [{"name": "advance", "arguments": {'seconds': 5}}], agent_context=context)
    error = _steps(env)[-2]
    assert read_episode(env.record_path)["messages"] == context["messages"]
    assert error["submitted_decision"] == [{"name": "advance", "arguments": {'seconds': 5}}]
    assert "messages" not in error and "agent_context" not in error


def test_context_available_when_recording_is_disabled(tmp_path):
    env = Environment(record_trajectory=False, record_dir=tmp_path / "unused")
    assert env.latest_observation is None
    env.reset()
    assert "Current Observation" in platform_turn_messages(env.latest_observation, env.latest_feedback, env.config.race if env.config else "terran")[1]["content"]
    env.close()
