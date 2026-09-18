"""Terran ability and SCV scouting facts, without control or hidden inference."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from sc2bench_env.interface.race_views import RaceObservationView

if TYPE_CHECKING:
    from sc2bench_env.backends.base import BackendSnapshot
    from sc2bench_env.runtime.task import Demand


def _known_int(value: Any) -> Optional[int]:
    return None if value is None else int(value)


def build_view(
    *, snapshot: BackendSnapshot, scout_demand: Optional[Demand] = None,
) -> RaceObservationView:
    info = snapshot.info
    orbital_count = _known_int(info.get("orbital_count", snapshot.buildings.get("orbital_command", 0)))
    scan_ready = _known_int(info.get("scan_ready", 0))
    # Scan and MULE have the same energy threshold; keep the old absent-key
    # compatibility fallback, but never replace an explicit unknown with zero.
    mule_ready = _known_int(info.get("mule_ready", info.get("scan_ready", 0)))
    energies = info.get("orbital_energies", [])
    abilities = {
        "scan_ready": scan_ready,
        "mule_ready": mule_ready,
        "orbital_energies": None if energies is None else list(energies),
    }
    scouting: Dict[str, Dict[str, Any]] = {}
    if scout_demand is not None:
        row: Dict[str, Any] = {
            "status": scout_demand.state.value,
            "route": list(scout_demand.route or ()) if scout_demand.route != "all" else None,
        }
        progress = dict(info.get("scout_progress") or {})
        if scout_demand.route == "all":
            row["mode"] = "all_expansions"
            actual_route = progress.get("route")
            row["route"] = list(actual_route) if isinstance(actual_route, (list, tuple)) else None
        moving_to = progress.get("moving_to")
        if moving_to:
            row["moving_to"] = str(moving_to)
        if "waypoint_index" in progress:
            row["waypoint_index"] = _known_int(progress["waypoint_index"])
        if "assigned" in progress:
            assigned = progress["assigned"]
            row["assigned"] = None if assigned is None else bool(assigned)
        scouting["scv"] = row
    return RaceObservationView(
        abilities=abilities,
        scouting=scouting,
        legacy_attributes={
            "orbital_count": orbital_count,
            "scan_ready": scan_ready,
            "mule_ready": mule_ready,
        },
    )
