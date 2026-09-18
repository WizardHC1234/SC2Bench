"""Decision-boundary synchronization without SC2 processes or model calls."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.base import BackendSnapshot
from sc2bench_env.backends.sharpy.backend import SharpyBackend, _Bridge, _ensure_runtime_paths
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.runtime.scheduler import DecisionTrigger, WaitPredicate


def publish(bridge, seconds=0):
    return bridge.on_frame(
        snapshot=BackendSnapshot(game_time_seconds=seconds, units={"scv": 12},
                                 buildings={"command_center": 1}, minerals=50,
                                 supply_used=12, supply_cap=15),
        in_production_units={}, in_progress_buildings={}, in_progress_research={},
        macro_errors={},
    )


@pytest.fixture
def simulated_worker(monkeypatch):
    """Use the actual start/submit/run_until/close bridge with a tiny game loop."""
    frames = threading.Event()

    def worker(backend):
        bridge = backend._bridge
        seconds = 0
        try:
            while not bridge.should_leave():
                boundary = publish(bridge, seconds)
                frames.set()
                if boundary:
                    bridge.wait_for_decision()
                if bridge.should_leave():
                    break
                seconds += 1
                # Simulate runner work, keeping non-blocking mode bounded in CPU.
                threading.Event().wait(0.01)
        finally:
            bridge.on_game_end("closed")

    monkeypatch.setattr(SharpyBackend, "_run_game_thread", worker)
    return frames


def test_default_freezes_reset_step_and_rejected_decision(tmp_path, simulated_worker):
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        initial = env.reset(EpisodeConfig(game_time_limit_seconds=None))
        bridge = env.backend._bridge
        assert bridge.blocking_decisions
        assert not bridge.advance_allowed.wait(0.05)
        assert env.backend.snapshot().game_time_seconds == initial.game_time_seconds == 0
        obs, _, done, _ = env.step([
            {"action": "wait", "any_of": [{"condition": "interval", "seconds": 3}]},
        ])
        assert not done and obs.game_time_seconds == 3
        assert not bridge.advance_allowed.wait(0.05)
        assert env.backend.snapshot().game_time_seconds == 3
        rejected, feedback, _, _ = env.step(None)
        assert feedback.events[0]["type"] == "decision_rejected"
        assert not bridge.advance_allowed.wait(0.05)
        assert rejected.game_time_seconds == env.backend.snapshot().game_time_seconds == 3
        obs, _, _, _ = env.step([
            {"action": "wait", "any_of": [{"condition": "interval", "seconds": 2}]},
        ])
        assert obs.game_time_seconds == 5
    finally:
        worker = env.backend._thread
        env.close()
    assert not worker.is_alive()


def test_non_blocking_option_preserves_between_step_progress(tmp_path, simulated_worker):
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(blocking_decisions=False, game_time_limit_seconds=None))
        bridge = env.backend._bridge
        assert not bridge.blocking_decisions
        before = env.backend.snapshot().game_time_seconds
        simulated_worker.clear()
        assert simulated_worker.wait(1)
        assert env.backend.snapshot().game_time_seconds > before
        env.step([{"action": "wait", "any_of": [{"condition": "interval", "seconds": 2}]}])
        before = env.backend.snapshot().game_time_seconds
        simulated_worker.clear()
        assert simulated_worker.wait(1)
        assert env.backend.snapshot().game_time_seconds > before
    finally:
        env.close()


@pytest.mark.parametrize("end", ["close", "game_end"])
def test_paused_worker_wakes_on_close_or_game_end_without_lock_deadlock(end):
    bridge = _Bridge()
    entered, returned = threading.Event(), threading.Event()

    def wait():
        entered.set()
        bridge.wait_for_decision()
        returned.set()

    worker = threading.Thread(target=wait, daemon=True)
    worker.start()
    try:
        assert entered.wait(1)
        assert not returned.wait(0.02)
        if end == "close":
            bridge.request_leave()
        else:
            bridge.on_game_end("Result.Victory")
        assert returned.wait(1)
    finally:
        bridge.request_leave()
        worker.join(1)
    assert not worker.is_alive()


def test_trigger_freezes_snapshot_before_notifying_caller():
    bridge = _Bridge()
    bridge.active_trigger = DecisionTrigger(
        interval_seconds=2, any_of=(WaitPredicate("interval", {"seconds": 2}),),
    )
    bridge.advance_allowed.set()
    assert not publish(bridge, 1)
    assert not bridge.decision_reached.is_set()
    assert publish(bridge, 2)
    assert bridge.decision_reached.is_set()
    assert not bridge.advance_allowed.is_set()
    assert bridge.active_trigger is None


def test_already_terminated_run_until_does_not_resume():
    backend = SharpyBackend()
    backend._bridge.on_game_end("Result.Victory")
    assert backend.run_until(DecisionTrigger())


def test_bot_refreshes_submitted_tasks_before_executing_resumed_frame(monkeypatch):
    _ensure_runtime_paths()
    from sc2bench_env.backends.sharpy import bot as bot_module

    bot = SimpleNamespace(
        bridge=MagicMock(), _macro_tasks=[], _sync_macro_tasks=MagicMock(),
        _collect_macro_errors=MagicMock(return_value={}),
        _collect_act_completed=MagicMock(return_value={}),
        _collect_scout_progress=MagicMock(return_value={}),
        _collect_combat_progress=MagicMock(return_value={}),
    )
    bot.bridge.on_frame.return_value = True
    bot.bridge.should_leave.return_value = False
    monkeypatch.setattr(bot_module, "read_snapshot", MagicMock(return_value=(BackendSnapshot(), {}, {}, {})))
    bot.adapter = MagicMock()
    order = []
    bot._sync_macro_tasks.side_effect = lambda: order.append("sync")
    bot.bridge.wait_for_decision.side_effect = lambda: order.append("resume")
    asyncio.run(bot_module.BenchBot.pre_step_execute(bot))
    assert order == ["sync", "resume", "sync"]
