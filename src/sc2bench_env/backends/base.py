"""Backend abstraction shared by FakeBackend and SharpyBackend."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.runtime.scheduler import DecisionTrigger
from sc2bench_env.runtime.task import Task
from sc2bench_env.runtime.task_manager import TaskUpdate


@dataclass
class BackendSnapshot:
    """Platform-facing state read from a backend."""

    game_time_seconds: float = 0.0
    minerals: int = 0
    vespene: int = 0
    supply_used: int = 0
    supply_cap: int = 0
    units: Dict[str, int] = field(default_factory=dict)
    buildings: Dict[str, int] = field(default_factory=dict)
    terminated: bool = False
    result: Optional[str] = None
    info: Dict[str, Any] = field(default_factory=dict)
    # Source-attributed ending, not inferred from a game's win/loss result.
    end_reason: Optional[str] = None

    def owned_count(self, action: str, target: str) -> int:
        if action == "build":
            return int(self.buildings.get(target, 0)) + int(
                (self.info.get("under_construction") or {}).get(target, 0)
            )
        if action == "train":
            return int(self.units.get(target, 0))
        if action == "expand":
            if "base_count" in self.info:
                return int(self.info["base_count"])
            return int(self.buildings.get("command_center", 0)) + int(
                self.buildings.get("orbital_command", 0)
            ) + int(self.buildings.get("planetary_fortress", 0))
        if action == "research":
            upgrades = self.info.get("upgrades") or []
            return 1 if target in upgrades else 0
        if action == "scan":
            return 0
        return 0


class Backend(ABC):
    """Minimal backend capability used by Environment."""

    def set_replay_path(self, path: Optional[Path]) -> None:
        """Optional capability; non-SC2 backends do not produce replays."""

    @abstractmethod
    def start_episode(self, config: EpisodeConfig) -> BackendSnapshot:
        raise NotImplementedError

    @abstractmethod
    def submit(self, tasks: List[Task]) -> None:
        """Register currently active tasks with the backend."""
        raise NotImplementedError

    @abstractmethod
    def run_until(self, trigger: DecisionTrigger) -> bool:
        """Advance until the next decision point. Return True if terminated."""
        raise NotImplementedError

    @abstractmethod
    def collect_updates(self) -> List[TaskUpdate]:
        """Return progress deltas since the previous collect call."""
        raise NotImplementedError

    @abstractmethod
    def snapshot(self) -> BackendSnapshot:
        raise NotImplementedError

    @abstractmethod
    def close_episode(self) -> None:
        raise NotImplementedError
