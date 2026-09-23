"""Shared clock for versus tests. Time moves only while both sides are released."""

from __future__ import annotations

from typing import Optional

from sc2bench_env.backends.base import Backend, BackendSnapshot
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.runtime.scheduler import DecisionTrigger
from sc2bench_env.runtime.task import Task
from sc2bench_env.runtime.task_manager import TaskUpdate


def _snapshot(game_time: float) -> BackendSnapshot:
    return BackendSnapshot(
        game_time_seconds=game_time,
        minerals=50,
        vespene=0,
        supply_used=12,
        supply_cap=15,
        units={"scv": 12},
        buildings={"command_center": 1},
        info={
            "backend": "versus-fake",
            "base_count": 1,
            "own_base_count": 1,
            "known_enemy_base_count": 1,
            "zones": ["zone_0"],
            "orbital_count": 0,
            "scan_ready": 0,
            "mule_ready": 0,
        },
    )


class _Side:
    def __init__(self) -> None:
        self.paused = True
        self.wake_at: Optional[float] = None
        self.snapshot = _snapshot(0.0)


class _FakePlayerBackend(Backend):
    def __init__(self, game: "FakeVersusGame", index: int) -> None:
        self._game = game
        self._index = index

    def start_episode(self, config: EpisodeConfig) -> BackendSnapshot:
        raise RuntimeError("VersusMatch starts the shared game")

    def submit(self, tasks: list[Task]) -> None:
        return None

    def run_until(self, trigger: DecisionTrigger) -> bool:
        raise RuntimeError("versus sides are released individually")

    def release(self, trigger: DecisionTrigger) -> None:
        self._game.release(self._index, trigger)

    def collect_updates(self) -> list[TaskUpdate]:
        return []

    def snapshot(self) -> BackendSnapshot:
        return self._game.sides[self._index].snapshot

    def close_episode(self) -> None:
        self._game.close()


class FakeVersusGame:
    def __init__(self) -> None:
        self.sides = (_Side(), _Side())
        self.backends = (_FakePlayerBackend(self, 0), _FakePlayerBackend(self, 1))
        self.now = 0.0
        self.limit: Optional[float] = None
        self._closed = False
        self._started = False

    def set_replay_path(self, path) -> None:
        return None

    def start(self, config: EpisodeConfig) -> tuple[BackendSnapshot, BackendSnapshot]:
        self.now = 0.0
        self.limit = config.game_time_limit_seconds
        self._closed = False
        self._started = True
        self.sides = (_Side(), _Side())
        self.backends = (_FakePlayerBackend(self, 0), _FakePlayerBackend(self, 1))
        return self.sides[0].snapshot, self.sides[1].snapshot

    def waiting(self) -> list[int]:
        return [
            index for index, side in enumerate(self.sides)
            if side.paused and not side.snapshot.terminated
        ]

    def wait_for_change(self, timeout: float) -> None:
        del timeout
        self._pump()

    def finished(self) -> bool:
        return self._closed or all(side.snapshot.terminated for side in self.sides)

    def failure(self) -> Optional[BaseException]:
        return None

    def release(self, index: int, trigger: DecisionTrigger) -> None:
        side = self.sides[index]
        if side.snapshot.terminated:
            side.paused = False
            return
        side.wake_at = self.now + float(trigger.interval_seconds)
        side.paused = False
        self._pump()

    def _pump(self) -> None:
        while not self.finished() and not any(side.paused for side in self.sides):
            wakes = [side.wake_at for side in self.sides]
            if any(wake is None for wake in wakes):
                return
            target = min(float(wake) for wake in wakes if wake is not None)
            if self.limit is not None and target >= float(self.limit):
                self._end("Result.Tie", "time_limit", float(self.limit))
                return
            self.now = target
            for side in self.sides:
                side.snapshot.game_time_seconds = self.now
                if side.wake_at is not None and side.wake_at <= self.now:
                    side.paused = True

    def _end(self, result: str, end_reason: str, game_time: float) -> None:
        self.now = game_time
        for side in self.sides:
            side.snapshot.game_time_seconds = game_time
            side.snapshot.terminated = True
            side.snapshot.result = result
            side.snapshot.end_reason = end_reason
            side.paused = False

    def close(self, end_reason: str = "closed_by_caller") -> None:
        if self._closed:
            return
        self._closed = True
        if self._started:
            for side in self.sides:
                if not side.snapshot.terminated:
                    side.snapshot.terminated = True
                    side.snapshot.end_reason = end_reason
                    side.paused = False
