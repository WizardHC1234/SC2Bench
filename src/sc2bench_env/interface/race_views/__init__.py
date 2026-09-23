"""Race-specific Observation assembly, independent of game libraries.

These builders read backend facts and active demands; they never execute acts,
select a strategy, choose a scout route or expose unsupported own races.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional

from sc2bench_env.interface.races import require_supported_own_race

if TYPE_CHECKING:
    from sc2bench_env.backends.base import BackendSnapshot
    from sc2bench_env.runtime.task import Demand


@dataclass
class RaceObservationView:
    abilities: Dict[str, Any] = field(default_factory=dict)
    scouting: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Old Python-only Observation attributes, never extra canonical sections.
    legacy_attributes: Dict[str, Any] = field(default_factory=dict)


def build_race_view(
    *, race: str, snapshot: BackendSnapshot, scout_demand: Optional[Demand] = None,
) -> RaceObservationView:
    require_supported_own_race(race)
    if race == "terran":
        from sc2bench_env.interface.race_views.terran import build_view

        return build_view(snapshot=snapshot, scout_demand=scout_demand)
    if race == "protoss":
        from sc2bench_env.interface.race_views.protoss import build_view

        return build_view(snapshot=snapshot, scout_demand=scout_demand)
    if race == "zerg":
        from sc2bench_env.interface.race_views.zerg import build_view

        return build_view(snapshot=snapshot, scout_demand=scout_demand)
    raise ValueError(f"No Observation view implemented for own race {race!r}")
