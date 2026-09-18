"""Internal demand / execution-task model (not Agent-visible IDs)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from uuid import uuid4

from sc2bench_env.interface.actions import GameAction
from sc2bench_env.interface.scouting import ScoutRoute


class DemandState(str, Enum):
    WAITING_TO_START = "waiting_to_start"
    WORKER_EN_ROUTE = "worker_en_route"
    UNDER_CONSTRUCTION = "under_construction"
    IN_PRODUCTION = "in_production"
    IN_PROGRESS = "in_progress"  # research queued / scanning
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    # Back-compat aliases used by Sharpy bridge while it migrates.
    WAITING = "waiting_to_start"
    EXECUTING = "in_progress"


ACTIVE_DEMAND_STATES = frozenset(
    {
        DemandState.WAITING_TO_START,
        DemandState.WORKER_EN_ROUTE,
        DemandState.UNDER_CONSTRUCTION,
        DemandState.IN_PRODUCTION,
        DemandState.IN_PROGRESS,
    }
)

# Demands that cancel() may still remove.
CANCELLABLE_STATES = frozenset({DemandState.WAITING_TO_START})


@dataclass
class Demand:
    """One ordered production / research / ability demand."""

    action: str
    target: str
    count: int = 1
    demand_id: str = field(default_factory=lambda: uuid4().hex)
    action_id: Optional[str] = None
    state: DemandState = DemandState.WAITING_TO_START
    produced: int = 0
    in_flight: Optional[int] = None  # Exact queued production when backend reports it.
    waiting_for: Optional[str] = None
    failure_reason: Optional[str] = None
    end_reason: Optional[str] = None
    order_index: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    # Snapshot helpers for backends that need absolute Sharpy targets.
    baseline_owned: int = 0
    # Object-level / multi-arg actions.
    to: Optional[str] = None
    route: Optional[ScoutRoute] = None
    style: Optional[str] = None
    units: Optional[dict[str, int]] = None
    group: Optional[str] = None
    withdrawing: bool = False
    command_revision: int = 0

    @classmethod
    def from_action(
        cls,
        action: GameAction,
        *,
        order_index: int,
        game_time: float,
        baseline_owned: int = 0,
    ) -> "Demand":
        count = 1 if action.count is None else int(action.count)
        return cls(
            action=action.action,
            target=action.target or "",
            count=count,
            action_id=action.action_id,
            order_index=order_index,
            created_at=game_time,
            updated_at=game_time,
            baseline_owned=baseline_owned,
            to=action.to,
            route=action.route,
            style=action.style,
            units=dict(action.units) if action.units else None,
        )

    @property
    def task_id(self) -> str:
        """Back-compat alias for backends still using task_id."""
        return self.demand_id

    @property
    def remaining(self) -> int:
        return max(0, self.count - self.produced)

    @property
    def completed(self) -> int:
        """Back-compat alias for produced."""
        return self.produced

    @completed.setter
    def completed(self, value: int) -> None:
        self.produced = value

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_DEMAND_STATES

    @property
    def is_cancellable(self) -> bool:
        return self.state in CANCELLABLE_STATES

    def identity(self) -> tuple[str, str]:
        return (self.action, self.target)

    def mark_failed(self, reason: str, game_time: float) -> None:
        self.state = DemandState.FAILED
        self.failure_reason = reason
        self.waiting_for = None
        self.updated_at = game_time

    def mark_completed(self, game_time: float) -> None:
        self.state = DemandState.COMPLETED
        self.produced = self.count
        self.waiting_for = None
        self.failure_reason = None
        self.updated_at = game_time

    def mark_cancelled(self, game_time: float) -> None:
        self.state = DemandState.CANCELLED
        self.waiting_for = None
        self.updated_at = game_time


# Temporary aliases while modules migrate off Task naming.
TaskStatus = DemandState
Task = Demand
ACTIVE_STATUSES = ACTIVE_DEMAND_STATES
