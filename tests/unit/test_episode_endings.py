"""Explicit ending attribution, clock cutoffs and non-blocking finalization."""

import json
import threading
from unittest.mock import MagicMock

import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.base import BackendSnapshot
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.backends.sharpy.backend import SharpyBackend, _Bridge, _ensure_runtime_paths
from sc2bench_env.interface.config import EpisodeConfig


def publish(bridge, seconds):
    return bridge.on_frame(
        snapshot=BackendSnapshot(game_time_seconds=seconds),
        in_production_units={}, in_progress_buildings={}, in_progress_research={},
        macro_errors={},
    )


@pytest.mark.parametrize("blocking", [True, False])
def test_deadline_uses_snapshot_clock_without_an_active_wait(blocking):
    bridge = _Bridge(blocking_decisions=blocking, max_game_time=1800)
    publish(bridge, 1773.2142857142858)
    assert not bridge.snapshot.terminated
    publish(bridge, 1800)
    assert bridge.snapshot.terminated
    assert bridge.snapshot.result == "Result.Tie"
    assert bridge.snapshot.end_reason == "time_limit"
    assert bridge.leave_requested and bridge.advance_allowed.is_set()
    assert bridge.decision_reached.is_set()
    bridge.on_game_end("Result.Defeat")
    bridge.on_game_end("Result.Tie")
    assert bridge.snapshot.result == "Result.Tie"
    assert bridge.snapshot.end_reason == "time_limit"


@pytest.mark.parametrize("result", ["Result.Victory", "Result.Defeat", "Result.Tie"])
def test_natural_game_result_keeps_its_cause_even_near_a_deadline(result):
    bridge = _Bridge(max_game_time=1800)
    publish(bridge, 1799.9)
    bridge.on_game_end(result)
    assert bridge.snapshot.result == result
    assert bridge.snapshot.end_reason == "game_ended"
    assert not bridge.leave_requested
    # Later close must not turn a real game result into caller interruption.
    bridge.request_leave(end_reason="closed_by_caller")
    assert bridge.snapshot.end_reason == "game_ended"


def test_error_is_not_masked_as_timeout_or_game_defeat():
    bridge = _Bridge(max_game_time=1)
    publish(bridge, 1)
    bridge.on_game_end("error:connection lost")
    assert bridge.snapshot.end_reason == "backend_error"
    assert bridge.snapshot.result == "error:connection lost"


def test_explicit_close_does_not_create_a_game_defeat():
    bridge = _Bridge()
    bridge.request_leave(end_reason="closed_by_caller")
    bridge.on_game_end("Result.Defeat")
    assert bridge.snapshot.terminated
    assert bridge.snapshot.end_reason == "closed_by_caller"
    assert bridge.snapshot.result is None


class EndingBackend(FakeBackend):
    def snapshot(self):
        snapshot = super().snapshot()
        snapshot.end_reason = getattr(self, "end_reason", None)
        return snapshot


@pytest.mark.parametrize("result,reason,status", [
    ("Result.Tie", "time_limit", "completed"),
    ("Result.Victory", "game_ended", "completed"),
    ("Result.Defeat", "game_ended", "completed"),
    ("Result.Tie", "game_ended", "completed"),
    ("error:connection lost", "backend_error", "failed"),
])
def test_game_ended_during_inference_ignores_new_decision_and_records_result(tmp_path, result, reason, status):
    backend = EndingBackend()
    env = Environment(backend, record_dir=tmp_path)
    try:
        env.reset()
        backend.terminated, backend.result, backend.end_reason = True, result, reason
        backend.game_time_seconds = 30
        obs, feedback, done, info = env.step([
            {"action": "build", "target": "supply_depot"}, {"action": "wait"},
        ])
        assert done and obs.terminated
        assert not env.task_manager.active_demands()
        assert feedback.events[0]["type"] == "episode_ended"
        assert info["result"] == result and info["end_reason"] == reason
        assert info["decision_applied"] is False
        from sc2bench_env.recording.reader import read_episode
        summary = read_episode(env.record_path)["summary"]
        assert summary["status"] == status and summary["end_reason"] == reason
        assert summary["result"] == (None if status == "failed" else result)
        assert summary["game_time_seconds"] == obs.game_time_seconds == 30
    finally:
        env.close()


def test_close_after_asynchronous_end_saves_final_state_without_a_fake_agent_call(tmp_path):
    backend = EndingBackend()
    env = Environment(backend, record_dir=tmp_path)
    env.reset()
    backend.terminated, backend.result, backend.end_reason = True, "Result.Tie", "time_limit"
    backend.game_time_seconds = 30
    directory = env.record_path
    env.close()
    from sc2bench_env.recording.reader import read_episode
    record = read_episode(directory)
    summary = record["summary"]
    assert summary["status"] == "completed" and summary["end_reason"] == "time_limit"
    assert summary["game_time_seconds"] == 30
    assert summary["decision_count"] == 0
    entries = record["steps"]
    assert entries[-1]["observation"]["game"]["game_time_seconds"] == 30
    assert entries[-1]["observation"]["terminated"] is True
    interactions = record
    assert interactions["interactions"] == []


@pytest.mark.parametrize("result", ["Result.Victory", "Result.Defeat", "Result.Tie"])
def test_custom_backend_natural_result_is_not_reclassified_by_clock(tmp_path, result):
    backend = EndingBackend()
    env = Environment(backend, record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=30))
        backend.terminated, backend.result = True, result
        backend.game_time_seconds = 31
        _, _, done, info = env.step(None)
        assert done and info["end_reason"] == "game_ended"
        assert env.recorder.summary["result"] == result
        assert env.recorder.summary["end_reason"] == "game_ended"
    finally:
        env.close()


def test_real_runner_does_not_receive_the_legacy_clock_limit(monkeypatch):
    _ensure_runtime_paths()
    import sc2
    from sc2.bot_ai import BotAI
    from sc2.data import Result
    from sc2bench_env.backends.sharpy import bot as bot_module

    run_game = MagicMock(return_value=Result.Victory)
    monkeypatch.setattr(sc2, "run_game", run_game)
    monkeypatch.setattr(sc2.maps, "get", MagicMock(return_value=object()))
    monkeypatch.setattr(bot_module, "BenchBot", MagicMock(return_value=MagicMock(spec=BotAI)))
    backend = SharpyBackend()
    backend._config = EpisodeConfig(game_time_limit_seconds=1800)
    backend._adapter = MagicMock()
    worker = threading.Thread(target=backend._run_game_thread, daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive()
    assert backend._game_error is None
    assert run_game.call_count == 1
    assert "game_time_limit" not in run_game.call_args.kwargs
    assert run_game.call_args.kwargs["realtime"] is False
    assert backend.snapshot().result == "Result.Victory"
    assert backend.snapshot().end_reason == "game_ended"
