"""Advance the backend until the next wait condition fires."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Protocol, Sequence, Tuple

from sc2bench_env.interface.actions import WaitAction, WaitCondition

# Platform contract: a decision wait cannot suppress agent control indefinitely.
# This is game time, relative to each step, not a wall-clock API deadline.
MAX_DECISION_WAIT_SECONDS = 60.0


class SupportsRunUntil(Protocol):
    def run_until(self, trigger: "DecisionTrigger") -> bool:
        """Run until trigger; return True if episode terminated."""


class SnapshotLike(Protocol):
    game_time_seconds: float
    minerals: int
    vespene: int
    supply_used: int
    supply_cap: int
    units: Dict[str, int]
    buildings: Dict[str, int]
    terminated: bool
    info: Dict[str, Any]


@dataclass(frozen=True)
class WaitPredicate:
    kind: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionTrigger:
    """When the environment should return control to the agent."""

    kind: str = "interval"
    interval_seconds: float = 5.0
    max_game_time_seconds: float | None = None
    any_of: Tuple[WaitPredicate, ...] = ()
    all_of: Tuple[WaitPredicate, ...] = ()
    # Legacy fields kept for older Fake/Sharpy paths while migrating.
    minerals_at_least: int | None = None
    vespene_at_least: int | None = None


def _to_predicate(condition: WaitCondition) -> WaitPredicate:
    return WaitPredicate(kind=condition.condition, params=dict(condition.params))


def trigger_from_wait(
    wait: WaitAction,
    *,
    default_interval_seconds: float,
    max_game_time_seconds: float | None,
) -> DecisionTrigger:
    """Map a trailing wait action onto a backend trigger."""
    any_of = tuple(_to_predicate(item) for item in wait.any_of)
    all_of = tuple(_to_predicate(item) for item in wait.all_of)

    def normalize(pred: WaitPredicate) -> WaitPredicate:
        if pred.kind != "interval":
            return pred
        seconds = pred.params.get("seconds")
        if seconds is None:
            seconds = default_interval_seconds
        return WaitPredicate(kind="interval", params={"seconds": float(seconds)})

    # Optional interval seconds have the same default in both combinators.
    normalized_any = [normalize(pred) for pred in any_of]
    all_of = tuple(normalize(pred) for pred in all_of)
    interval = default_interval_seconds
    for pred in normalized_any:
        if pred.kind == "interval":
            interval = pred.params["seconds"]
    if not normalized_any and not all_of:
        normalized_any = [
            WaitPredicate(kind="interval", params={"seconds": float(default_interval_seconds)})
        ]
        interval = float(default_interval_seconds)

    # Back-compat convenience fields for simple resource waits.
    minerals_at_least = None
    vespene_at_least = None
    for pred in list(normalized_any) + list(all_of):
        if pred.kind == "resource_at_least":
            if pred.params.get("resource") == "minerals":
                minerals_at_least = int(pred.params.get("amount", 0))
            elif pred.params.get("resource") == "vespene":
                vespene_at_least = int(pred.params.get("amount", 0))

    return DecisionTrigger(
        kind="wait",
        interval_seconds=interval,
        max_game_time_seconds=max_game_time_seconds,
        any_of=tuple(normalized_any),
        all_of=all_of,
        minerals_at_least=minerals_at_least,
        vespene_at_least=vespene_at_least,
    )


def _check_predicate(
    pred: WaitPredicate,
    *,
    snapshot: SnapshotLike,
    wait_started_at: float,
) -> bool:
    kind = pred.kind
    params = pred.params
    if kind == "interval":
        seconds = float(params.get("seconds") or 0.0)
        return snapshot.game_time_seconds >= wait_started_at + seconds
    if kind == "resource_at_least":
        resource = params.get("resource")
        amount = int(params.get("amount", 0))
        if resource == "minerals":
            return snapshot.minerals >= amount
        if resource == "vespene":
            return snapshot.vespene >= amount
        return False
    if kind == "supply_left_at_most":
        left = int(snapshot.supply_cap) - int(snapshot.supply_used)
        return left <= int(params.get("amount", 0))
    if kind == "unit_count_at_least":
        unit = str(params.get("unit") or "").lower()
        return int(snapshot.units.get(unit, 0)) >= int(params.get("count", 0))
    if kind == "building_count_at_least":
        building = str(params.get("building") or params.get("unit") or "").lower()
        return int(snapshot.buildings.get(building, 0)) >= int(params.get("count", 0))
    if kind == "scan_ready":
        ready = int(snapshot.info.get("scan_ready", 0))
        needed = params.get("count")
        if needed is None:
            return ready >= 1
        return ready >= int(needed)
    if kind == "game_time_at_least":
        return snapshot.game_time_seconds >= float(params.get("seconds", 0))
    if kind == "zone_under_attack":
        zone = str(params.get("zone") or "").lower()
        attacked = snapshot.info.get("zones_under_attack") or []
        return zone in {str(item).lower() for item in attacked}
    return False


def trigger_satisfied(
    trigger: DecisionTrigger,
    *,
    snapshot: SnapshotLike,
    wait_started_at: float,
) -> bool:
    """Return for terminal state, the fixed safety cap, or satisfied conditions."""
    if snapshot.terminated:
        return True
    if (
        trigger.max_game_time_seconds is not None
        and snapshot.game_time_seconds >= trigger.max_game_time_seconds
    ):
        return True
    if snapshot.game_time_seconds >= wait_started_at + MAX_DECISION_WAIT_SECONDS:
        return True

    any_preds: Sequence[WaitPredicate] = trigger.any_of
    all_preds: Sequence[WaitPredicate] = trigger.all_of

    def ok(pred: WaitPredicate) -> bool:
        return _check_predicate(pred, snapshot=snapshot, wait_started_at=wait_started_at)

    any_ok = True if not any_preds else any(ok(pred) for pred in any_preds)
    all_ok = all(ok(pred) for pred in all_preds)
    return bool(any_ok and all_ok)


class Scheduler:
    """Thin wrapper kept separate so later phases can swap trigger policies."""

    def run_until_next_decision(
        self,
        backend: SupportsRunUntil,
        trigger: DecisionTrigger,
    ) -> bool:
        return backend.run_until(trigger)
