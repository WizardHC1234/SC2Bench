"""One engine bookmark plus the platform state needed to return to it.

A savepoint is a sequential fork point, not a parallel copy of the match.
Creating another one replaces the single engine bookmark and invalidates
older handles. Restoring returns the same episode to that point so the next
decisions can be recorded as a separate branch.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Optional

from sc2bench_env.backends.base import BackendSnapshot


class SavepointError(RuntimeError):
    """The episode is not at a boundary where a savepoint can be used."""

    def __init__(self, message: str, *, results: Optional[tuple] = None) -> None:
        super().__init__(message)
        self.results = results


@dataclass(frozen=True)
class SavepointHandle:
    """Public proof that a restore still refers to the current bookmark."""

    episode_id: str
    generation: int
    label: str
    base_game_time_seconds: float
    engine_state_hash: str


@dataclass(frozen=True)
class BranchResult:
    """What one sequential branch returned when explored from a savepoint."""

    branch_id: str
    value: Any = None
    error: Optional[BaseException] = None


@dataclass
class PlatformCheckpoint:
    """Private copy held by Environment. Callers only receive the handle."""

    handle: SavepointHandle
    bookmark: str
    task_state: dict[str, Any]
    runtime_state: dict[str, Any]
    previous_observation: Optional[dict[str, Any]]
    recorder_cursor: Optional[dict[str, Any]]
    context: Any = None


def stable_hash(payload: Mapping[str, Any]) -> str:
    """Hash a JSON-stable mapping. Object identity is not part of the value."""
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def describe_engine(snapshot: BackendSnapshot) -> str:
    """Short field dump used when a restored frame does not match its savepoint."""
    info = snapshot.info or {}
    return (
        f"time={float(snapshot.game_time_seconds):.3f} "
        f"minerals={int(snapshot.minerals)} vespene={int(snapshot.vespene)} "
        f"supply={int(snapshot.supply_used)}/{int(snapshot.supply_cap)} "
        f"units={dict(snapshot.units)} buildings={dict(snapshot.buildings)} "
        f"under={dict(info.get('under_construction') or {})} "
        f"upgrades={sorted(str(item) for item in (info.get('upgrades') or []))} "
        f"terminated={bool(snapshot.terminated)} result={snapshot.result} "
        f"end_reason={snapshot.end_reason}"
    )


def engine_state_hash(snapshot: BackendSnapshot) -> str:
    """Signature of the published engine frame, with floats rounded to milliseconds."""
    info = snapshot.info or {}
    upgrades = info.get("upgrades") or []
    return stable_hash({
        "game_time_seconds": round(float(snapshot.game_time_seconds), 3),
        "minerals": int(snapshot.minerals),
        "vespene": int(snapshot.vespene),
        "supply_used": int(snapshot.supply_used),
        "supply_cap": int(snapshot.supply_cap),
        "units": {str(key): int(value) for key, value in dict(snapshot.units).items()},
        "buildings": {str(key): int(value) for key, value in dict(snapshot.buildings).items()},
        "under_construction": {
            str(key): int(value)
            for key, value in dict(info.get("under_construction") or {}).items()
        },
        "upgrades": sorted(str(item) for item in upgrades),
        "terminated": bool(snapshot.terminated),
        "result": snapshot.result,
        "end_reason": snapshot.end_reason,
    })
