"""Advance the backend until the requested game-time interval elapses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sc2bench_env.interface.actions import AdvanceAction


class SupportsRunUntil(Protocol):
    def run_until(self, trigger: "DecisionTrigger") -> bool:
        """Run until trigger; return True if episode terminated."""


class SnapshotLike(Protocol):
    game_time_seconds: float
    terminated: bool


@dataclass(frozen=True)
class DecisionTrigger:
    """When the environment should return control to the agent."""

    kind: str = "advance"
    interval_seconds: float = 5.0
    max_game_time_seconds: float | None = None


def trigger_from_advance(
    advance: AdvanceAction,
    *,
    max_game_time_seconds: float | None,
) -> DecisionTrigger:
    """Map a trailing advance action onto a backend trigger."""
    return DecisionTrigger(
        kind="advance",
        interval_seconds=float(advance.seconds),
        max_game_time_seconds=max_game_time_seconds,
    )


def trigger_satisfied(
    trigger: DecisionTrigger,
    *,
    snapshot: SnapshotLike,
    wait_started_at: float,
) -> bool:
    """Return for terminal state, the episode time limit, or elapsed seconds."""
    if snapshot.terminated:
        return True
    if (
        trigger.max_game_time_seconds is not None
        and snapshot.game_time_seconds >= trigger.max_game_time_seconds
    ):
        return True
    return snapshot.game_time_seconds >= wait_started_at + float(trigger.interval_seconds)


class Scheduler:
    """Thin wrapper kept separate so later phases can swap trigger policies."""

    def run_until_next_decision(
        self,
        backend: SupportsRunUntil,
        trigger: DecisionTrigger,
    ) -> bool:
        return backend.run_until(trigger)
