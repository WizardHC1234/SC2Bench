"""One SC2 game, two BenchBots. Each bot pauses on its own advance."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

from sc2bench_env.backends.base import Backend, BackendSnapshot
from sc2bench_env.backends.sharpy.backend import SharpyBackend, _parse_race
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.runtime.scheduler import DecisionTrigger
from sc2bench_env.runtime.task import Task
from sc2bench_env.runtime.task_manager import TaskUpdate

logger = logging.getLogger("sc2bench_env.backends.sharpy.versus")


def _disconnect_after_recorded_result(exc: BaseException, players) -> bool:
    """python-sc2 quits both clients, and the first cleanup closes the other socket."""
    if not isinstance(exc, ConnectionError):
        return False
    return all(
        player._bridge.snapshot.terminated
        and player._bridge.snapshot.end_reason not in {None, "backend_error"}
        and not str(player._bridge.snapshot.end_reason).startswith("error:")
        for player in players
    )


def _side_config(config: EpisodeConfig, *, race: str, enemy_race: str) -> EpisodeConfig:
    return EpisodeConfig(
        race=race,
        enemy_race=enemy_race,
        map_name=config.map_name,
        opponent=config.opponent,
        game_time_limit_seconds=config.game_time_limit_seconds,
        seed=config.seed,
        blocking_decisions=True,
        enemy_style=config.enemy_style,
        realtime=config.realtime,
    )


class _SharpyPlayerBackend(Backend):
    def __init__(self, game: "SharpyVersusGame", index: int) -> None:
        self._game = game
        self._index = index

    def _player(self) -> SharpyBackend:
        return self._game.players[self._index]

    def start_episode(self, config: EpisodeConfig) -> BackendSnapshot:
        raise RuntimeError("VersusMatch starts the shared game")

    def submit(self, tasks: list[Task]) -> None:
        self._player().submit(tasks)

    def run_until(self, trigger: DecisionTrigger) -> bool:
        raise RuntimeError("versus sides are released individually")

    def release(self, trigger: DecisionTrigger) -> None:
        self._player().release(trigger)

    def collect_updates(self) -> list[TaskUpdate]:
        return self._player().collect_updates()

    def snapshot(self) -> BackendSnapshot:
        return self._player().snapshot()

    def close_episode(self) -> None:
        self._game.close()


class SharpyVersusGame:
    def __init__(self, *, startup_timeout_seconds: float = 120.0) -> None:
        self.startup_timeout_seconds = startup_timeout_seconds
        self.players = (SharpyBackend(), SharpyBackend())
        self.backends = (_SharpyPlayerBackend(self, 0), _SharpyPlayerBackend(self, 1))
        self._notify = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[BaseException] = None
        self._closed = False
        self._replay_path: Optional[Path] = None

    def set_replay_path(self, path: Optional[Path]) -> None:
        self._replay_path = path

    def start(self, config: EpisodeConfig) -> tuple[BackendSnapshot, BackendSnapshot]:
        left = _side_config(config, race=config.race, enemy_race=config.enemy_race)
        right = _side_config(config, race=config.enemy_race, enemy_race=config.race)
        self.players[0].set_replay_path(self._replay_path)
        self.players[1].set_replay_path(None)
        self.players[0].prepare_for_versus(left)
        self.players[1].prepare_for_versus(right)
        self.players[0]._bridge.companions = [self.players[1]._bridge]
        self.players[1]._bridge.companions = [self.players[0]._bridge]
        self.players[0]._bridge.notify = self._notify
        self.players[1]._bridge.notify = self._notify
        self._error = None
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="sc2bench-versus", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + self.startup_timeout_seconds
        for player in self.players:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not player._bridge.ready.wait(timeout=remaining):
                self.close()
                raise TimeoutError(
                    f"SC2/Sharpy versus did not become ready within {self.startup_timeout_seconds}s"
                )
        if self._error is not None:
            self.close()
            raise RuntimeError(f"SC2/Sharpy versus failed to start: {self._error}") from self._error
        return self.players[0].snapshot(), self.players[1].snapshot()

    def waiting(self) -> list[int]:
        return [index for index, player in enumerate(self.players) if player.is_waiting()]

    def wait_for_change(self, timeout: float) -> None:
        self._notify.wait(timeout=timeout)
        self._notify.clear()

    def finished(self) -> bool:
        if self._error is not None:
            return True
        # on_end marks both snapshots terminated before the host saves the replay.
        # Wait until that thread exits so the replay file is already written.
        if self._thread is not None and self._thread.is_alive():
            return False
        if not self._started_ready():
            return False
        return all(player.snapshot().terminated for player in self.players)

    def _started_ready(self) -> bool:
        return all(player._bridge.ready.is_set() for player in self.players)

    def failure(self) -> Optional[BaseException]:
        return self._error

    def close(self, end_reason: str = "closed_by_caller") -> None:
        if self._closed:
            return
        self._closed = True
        for player in self.players:
            player._bridge.request_leave(end_reason=end_reason)
            player._bridge.decision_reached.set()
            player._bridge.advance_allowed.set()
        self._notify.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=30.0)
        self._thread = None

    def _run(self) -> None:
        from sc2bench_env.backends.sharpy.compat import register_modern_terran_abilities
        from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths

        register_modern_terran_abilities()
        try:
            _ensure_runtime_paths()
            import asyncio
            import signal

            from sc2 import maps, run_game
            from sc2.player import Bot

            from sc2bench_env.backends.sharpy.bot import BenchBot

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            original_signal = signal.signal

            def threadsafe_signal(signum, handler):  # type: ignore[no-untyped-def]
                try:
                    return original_signal(signum, handler)
                except ValueError:
                    return signal.SIG_DFL

            signal.signal = threadsafe_signal  # type: ignore[assignment]
            left, right = self.players
            assert left._config is not None and right._config is not None
            assert left._adapter is not None and right._adapter is not None
            kwargs: dict = {"realtime": False}
            if self._replay_path is not None:
                kwargs["save_replay_as"] = str(self._replay_path)
            if left._config.seed is not None:
                kwargs["random_seed"] = int(left._config.seed)
            results = run_game(
                maps.get(left._config.map_name),
                [
                    Bot(_parse_race(left._config.race), BenchBot(left._bridge, left._adapter, "SC2Bench")),
                    Bot(_parse_race(right._config.race), BenchBot(right._bridge, right._adapter, "SC2Bench2")),
                ],
                **kwargs,
            )
            if not isinstance(results, (list, tuple)):
                results = (results, results)
            for player, result in zip(self.players, results):
                if not player._bridge.snapshot.terminated:
                    if result is None:
                        raise RuntimeError("SC2 runner returned without a game result")
                    player._bridge.on_game_end(str(result))
        except BaseException as exc:
            if _disconnect_after_recorded_result(exc, self.players):
                logger.warning("SC2 closed the versus connection after both results were recorded: %s", exc)
            else:
                logger.exception("Sharpy versus game thread failed")
                self._error = exc
                for player in self.players:
                    if not player._bridge.snapshot.terminated:
                        player._bridge.on_game_end(f"error:{exc}")
        finally:
            for player in self.players:
                player._bridge.stopped.set()
                player._bridge.ready.set()
                player._bridge.decision_reached.set()
                player._bridge.advance_allowed.set()
            self._notify.set()
