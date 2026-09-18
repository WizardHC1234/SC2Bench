"""Durable per-episode records without running StarCraft II."""

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

def _lines(directory):
    return read_episode(directory)["steps"]


def test_episode_is_saved_before_close_and_inputs_are_frozen(tmp_path):
    env = Environment(record_dir=tmp_path)
    initial = env.reset()
    directory = env.record_path
    assert directory is not None
    assert read_episode(directory)["metadata"]["config"]["map_name"] == "KairosJunctionLE"
    assert read_episode(directory)["platform_prompt"] == env.get_system_prompt()
    assert {path.name for path in directory.iterdir()} == {"episode.txt", "interactions.jsonl"}
    assert _lines(directory)[0]["observation"] == initial.to_dict()
    assert "info" not in _lines(directory)[0]["observation"]
    assert "units" not in _lines(directory)[0]["observation"]
    assert read_episode(directory)["summary"] is None

    decision = [{"action": "wait"}]
    obs, feedback, terminated, info = env.step(decision)
    line = _lines(directory)[-1]
    assert line["submitted_decision"] == decision
    assert line["parsed_decision"][-1]["action"] == "wait"
    assert line["observation"] == obs.to_dict()
    assert line["feedback"] == feedback.to_dict()
    assert line["game_time_after_seconds"] > line["game_time_before_seconds"]
    assert line["wall_time_seconds"] >= 0
    assert line["observation_char_count"] == len(json.dumps(obs.to_dict(), ensure_ascii=False, separators=(",", ":")))
    decision[0]["action"] = "mutated"
    assert env.trajectory()["steps"][-1]["submitted_decision"][0]["action"] == "wait"
    env.close()
    assert read_episode(directory)["summary"]["status"] == "interrupted"


def test_rejections_are_saved_without_advancing_game(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    decision = [{"action": "build", "target": "barracks"}]  # no trailing wait
    env.step(decision)
    entry = _lines(env.record_path)[-1]
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
    _, _, terminated, _ = env.step([{"action": "wait"}])
    assert terminated
    directory = env.record_path
    summary = read_episode(directory)["summary"]
    assert summary["status"] == "completed"
    assert summary["end_reason"] == "time_limit"
    assert summary["result"] == "Result.Tie"
    env.close()
    env.close()
    assert read_episode(directory)["summary"] == summary
    assert len([entry for entry in _lines(directory) if entry["type"] == "end"]) == 1


def test_reset_creates_new_directory_and_finalizes_previous(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    first = env.record_path
    env.reset()
    second = env.record_path
    assert first != second
    assert read_episode(first)["summary"]["status"] == "interrupted"
    assert len(_lines(second)) == 1
    env.close()


def test_recording_can_be_disabled(tmp_path):
    directory = tmp_path / "disabled"
    env = Environment(record_trajectory=False, record_dir=directory)
    env.reset()
    env.step([{"action": "wait"}])
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
        env.step([{"action": "wait"}])
    entries = _lines(env.record_path)
    assert entries[0]["type"] == "reset"
    assert entries[-2]["type"] == "error"
    assert entries[-2]["submitted_decision"] == [{"action": "wait"}]
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
    assert _lines(env.record_path)[0]["type"] == "error"


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
    env.step([{"action": "train", "target": "marine", "count": float("nan")}, {"action": "wait"}])
    assert _lines(env.record_path)[-1]["submitted_decision"][0]["count"] == {"non_json_number": "nan"}
    env.close()


def test_keyboard_interrupt_is_not_a_game_defeat(tmp_path):
    class InterruptedBackend(FakeBackend):
        def run_until(self, trigger):
            raise KeyboardInterrupt()

    env = Environment(InterruptedBackend(), record_dir=tmp_path)
    env.reset()
    with pytest.raises(KeyboardInterrupt):
        env.step([{"action": "wait"}])
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
    decision = [{"action": "build", "target": "supply_depot"}, {"action": "wait"}]
    env.step(decision, retry_ids=["transport-1"])
    entry = _lines(env.record_path)[-1]
    assert entry["submitted_decision"] == decision
    assert all("action_id" not in action for action in entry["parsed_decision"])
    env.close()


def test_null_input_is_preserved_as_null(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    env.step(None)
    assert _lines(env.record_path)[-1]["submitted_decision"] is None
    env.close()


def test_text_context_uses_pre_decision_observation_and_full_sections(tmp_path):
    env = Environment(record_dir=tmp_path)
    initial = env.reset()
    messages = env.get_context()
    for section in ("[Zone State]", "[Scouting]", "[Combat]", "[Abilities]", "[Recent Events]"):
        assert section in messages[1]["content"]
    assert "zone_0" in messages[1]["content"]
    env.step([{"action": "wait"}])
    entry = _lines(env.record_path)[-1]
    for key in ("messages", "context_source", "text_observation", "returned_text_observation", "agent_context"):
        assert key not in entry
    interactions = read_episode(env.record_path)["interactions"]
    assert interactions[0]["input"]["messages"] is None
    assert interactions[0]["output"]["assistant_content"] is None
    assert "platform_reference_messages" not in interactions[0]
    assert not (env.record_path / "context.txt").exists()
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
    env.step([{"action": "wait"}], agent_context=agent_context)
    entry = _lines(env.record_path)[-1]
    interaction = read_episode(env.record_path)["interactions"][0]
    assert interaction["input"]["source"] == "agent"
    assert interaction["input"]["messages"] == agent_context["messages"]
    assert interaction["input"]["text_observation"] == agent_context["text_observation"]
    assert interaction["messages_transcript"] == agent_context["messages_transcript"]
    assert interaction["output"]["assistant_content"] == "原始回复"
    assert interaction["metadata"]["usage"]["input_tokens"] == 123
    agent_context["messages"][0]["content"] = "changed"
    assert env.recorder._interactions[0]["input"]["messages"][0]["content"] == "custom skill"
    interaction = read_episode(env.record_path)["interactions"][0]
    assert interaction["input"]["messages"][1]["content"] == "实际输入"
    assert interaction["output"]["assistant_content"] == "原始回复"
    assert interaction["output"]["submitted_decision"] == [{"action": "wait"}]
    assert interaction["step_index"] == entry["step_index"]
    assert "environment_response" not in interaction
    assert "validation" not in interaction
    assert "agent_context" not in interaction
    assert "agent_context" not in entry
    env.close()


def test_compact_episode_keeps_full_actual_inputs_without_repeating_system_text(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    system = "Agent harness instructions and skills" * 100
    for index in range(3):
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": f"round {index}"}]
        env.step([{"action": "wait"}], agent_context={
            "messages": messages, "assistant_content": '[{"action":"wait"}]',
        })
    directory = env.record_path
    env.close()
    assert {path.name for path in directory.iterdir()} == {"episode.txt", "interactions.jsonl"}
    rows = [json.loads(line) for line in (directory / "interactions.jsonl").read_text(encoding="utf-8").splitlines()]
    definitions = [row for row in rows if row["type"] == "system_message"]
    assert len(definitions) == 1 and definitions[0]["content"] == system
    turns = [row for row in rows if row["type"] == "step"]
    assert len(turns) == 3
    assert all("content" not in turn["agent_interaction"]["input"]["messages"][0]
               for turn in turns)
    assert all(turn["agent_interaction"]["input"]["messages"][0]["content_ref"] == definitions[0]["id"]
               for turn in turns)
    record = read_episode(directory)
    assert [row["input"]["messages"][0]["content"] for row in record["interactions"]] == [system] * 3
    assert [row["input"]["messages"][1]["content"] for row in record["interactions"]] == [
        "round 0", "round 1", "round 2",
    ]
    assert record["summary"]["decision_count"] == 3


def test_changed_system_input_gets_its_own_definition(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    for system in ("first", "second", "first"):
        env.step([{"action": "wait"}], agent_context={
            "messages": [{"role": "system", "content": system}], "assistant_content": "ok",
        })
    directory = env.record_path
    env.close()
    rows = [json.loads(line) for line in (directory / "interactions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len([row for row in rows if row["type"] == "system_message"]) == 2
    assert [row["input"]["messages"][0]["content"] for row in read_episode(directory)["interactions"]] == [
        "first", "second", "first",
    ]


def test_platform_prompt_is_reused_from_episode_text(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    messages = env.get_context()
    env.step([{"action": "wait"}], agent_context={
        "messages": messages,
        "messages_transcript": [*messages, {"role": "assistant", "content": "ok"}],
        "assistant_content": "ok",
    })
    directory = env.record_path
    env.close()
    rows = [json.loads(line) for line in (directory / "interactions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert not any(row["type"] == "system_message" for row in rows)
    interaction = next(row["agent_interaction"] for row in rows if row["type"] == "step")
    assert "content_ref" in interaction["input"]["messages"][0]
    assert "content_ref" in interaction["messages_transcript"][0]
    restored = read_episode(directory)["interactions"][0]
    assert restored["input"]["messages"] == messages
    assert restored["messages_transcript"][0] == messages[0]


def test_interactions_jsonl_is_appended_each_round_and_finalized_on_close(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    path = env.record_path / "interactions.jsonl"
    assert read_episode(env.record_path)["interactions"] == []
    for index in range(2):
        env.step([{"action": "wait"}], agent_context={
            "messages": env.get_context(), "assistant_content": f"response {index}",
        })
        assert len(read_episode(env.record_path)["interactions"]) == index + 1
    before_close = path.read_bytes()
    env.close()
    assert path.read_bytes().startswith(before_close)
    assert read_episode(env.record_path)["steps"][-1]["type"] == "end"
    assert read_episode(env.record_path)["summary"]["status"] == "interrupted"


def test_rejected_action_keeps_text_context(tmp_path):
    env = Environment(record_dir=tmp_path)
    env.reset()
    messages = env.get_context()
    env.step([{"action": "oops"}], agent_context={"messages": messages, "assistant_content": "bad action"})
    entry = _lines(env.record_path)[-1]
    interaction = read_episode(env.record_path)["interactions"][0]
    assert interaction["input"]["messages"] == messages
    assert interaction["step_index"] == entry["step_index"]
    assert not entry["validation"]["accepted"]
    assert "Previous Feedback" in env.get_context()[1]["content"]
    env.close()


def test_recording_does_not_automatically_build_reference_context(tmp_path, monkeypatch):
    env = Environment(record_dir=tmp_path)
    env.reset()
    def unexpected_context():
        raise AssertionError("reference context should only be requested explicitly")
    monkeypatch.setattr(env, "get_context", unexpected_context)
    env.step([{"action": "wait"}])
    env.close()


def test_exception_keeps_real_agent_input_output_outside_trajectory(tmp_path):
    env = Environment(BrokenBackend(), record_dir=tmp_path)
    env.reset()
    context = {"messages": [{"role": "user", "content": "real input"}], "assistant_content": "real output"}
    with pytest.raises(RuntimeError, match="backend exploded"):
        env.step([{"action": "wait"}], agent_context=context)
    error = _lines(env.record_path)[-2]
    interaction = read_episode(env.record_path)["interactions"][0]
    assert interaction["step_index"] == error["step_index"]
    assert interaction["input"]["messages"] == context["messages"]
    assert interaction["output"]["assistant_content"] == "real output"
    assert "messages" not in error and "agent_context" not in error


def test_context_available_when_recording_is_disabled(tmp_path):
    env = Environment(record_trajectory=False, record_dir=tmp_path / "unused")
    with pytest.raises(RuntimeError, match="reset"):
        env.get_context()
    env.reset()
    assert "Current Observation" in env.get_context()[1]["content"]
    env.close()
