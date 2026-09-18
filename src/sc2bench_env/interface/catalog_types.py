"""Dependency-free action metadata shared by race catalogs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Tuple


@dataclass(frozen=True)
class TargetSpec:
    """One legal target under a high-level action verb."""

    name: str
    action: str
    kind: str  # building | addon | unit | research | morph | ability
    description: str
    minerals: int = 0
    vespene: int = 0
    energy: int = 0
    supply: int = 0
    # Approximate game seconds per item from start to ready/finished for
    # construction/production/research/morph; FakeBackend scheduling only
    # for abilities/scouting. Never an Agent-visible task deadline.
    base_time_seconds: float = 0.0
    prerequisites: Tuple[str, ...] = ()
    # append | idempotent | replace | oneshot | morph
    semantics: str = "append"
    # Where the action succeeds for the Agent-visible demand.
    success_boundary: str = ""
    # Optional producer / research building / morph source.
    produced_at: str = ""
    morph_from: str = ""

    def cost_dict(self) -> Dict[str, int]:
        return {
            "minerals": int(self.minerals),
            "vespene": int(self.vespene),
            "energy": int(self.energy),
            "supply": int(self.supply),
            "build_time": int(self.base_time_seconds),
        }

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["prerequisites"] = list(self.prerequisites)
        return payload


@dataclass(frozen=True)
class CatalogData:
    """Race-specific reference data; execution stays in the backend."""

    race: str
    targets: Tuple[TargetSpec, ...]
    table_legend: Tuple[str, ...]
    target_notes: Mapping[str, Tuple[str, ...]]
