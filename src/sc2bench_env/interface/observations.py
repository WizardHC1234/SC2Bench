"""Standard Observation returned to external agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

WORKER_UNIT_NAMES = frozenset({"scv", "probe", "drone", "mule"})

OBSERVATION_SECTIONS = (
    ("game", "Game"), ("economy", "Economy"), ("map_control", "Map Control"),
    ("map_topology", "Map Topology"), ("zone_state", "Zone State"),
    ("production_priority", "Production Priority"), ("production", "Production Capacity"), ("building", "Building"),
    ("training", "Training"), ("own_forces", "Own Forces"), ("research", "Research"),
    ("structures", "Structures"), ("abilities", "Abilities"),
    ("combat", "Combat"), ("scouting", "Scouting"), ("recent_events", "Recent Events"),
    ("terminated", "Terminated"),
)


def render_observation_text(observation: Dict[str, Any]) -> str:
    """One semantic text view for agent context and Observation.section_lines.

    Compatibility count aliases and backend diagnostics are not repeated;
    macro quantities/blockers are summarized by type; the full per-order priority
    table also preserves progress/queues/states, explicitly labeled accepted work.
    """
    from sc2bench_env.interface.observation_text import render_text

    return render_text(observation, OBSERVATION_SECTIONS)

@dataclass
class ResourcesView:
    minerals: int = 0
    vespene: int = 0
    supply_used: int = 0
    supply_cap: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GameView:
    game_time_seconds: float = 0.0
    game_time_limit_seconds: Optional[float] = None
    seconds_remaining: Optional[float] = None
    race: str = "terran"
    enemy_race: str = "terran"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "game_time_seconds": self.game_time_seconds,
            "race": self.race,
            "enemy_race": self.enemy_race,
        }
        if self.game_time_limit_seconds is not None:
            payload["game_time_limit_seconds"] = self.game_time_limit_seconds
        if self.seconds_remaining is not None:
            payload["seconds_remaining"] = self.seconds_remaining
        return payload


@dataclass
class EconomyView:
    minerals: int = 0
    vespene: int = 0
    supply_used: int = 0
    supply_cap: int = 0
    supply_left: int = 0
    worker_count: int = 0
    ideal_worker_count: Optional[int] = None
    army_supply: int = 0
    mineral_income_per_minute: Optional[float] = None
    vespene_income_per_minute: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MapControlView:
    own_base_count: int = 0
    known_enemy_base_count: int = 0
    unconfirmed_expansion_count: int = 0
    base_resources: List[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OwnForcesView:
    """Living own units. ``army`` is total army; assigned/free split combat ownership."""

    workers: Dict[str, int] = field(default_factory=dict)
    army: Dict[str, int] = field(default_factory=dict)
    assigned: Dict[str, int] = field(default_factory=dict)
    free: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "workers": dict(self.workers),
            "army": dict(self.army),
            "assigned": dict(self.assigned),
            "free": dict(self.free) if self.free or self.assigned else dict(self.army),
        }
        return payload


@dataclass
class Observation:
    """Agent-visible state grouped by semantic sections."""

    game_time_seconds: float = 0.0
    race: str = "terran"
    enemy_race: str = "terran"
    resources: ResourcesView = field(default_factory=ResourcesView)
    game: GameView = field(default_factory=GameView)
    economy: EconomyView = field(default_factory=EconomyView)
    map_control: MapControlView = field(default_factory=MapControlView)
    zone_state: List[dict[str, Any]] = field(default_factory=list)
    map_topology: Dict[str, Any] = field(default_factory=dict)
    production_priority: List[dict[str, Any]] = field(default_factory=list)
    production: Optional[List[dict[str, Any]]] = None
    own_forces: OwnForcesView = field(default_factory=OwnForcesView)
    abilities: Dict[str, Any] = field(default_factory=dict)
    # Own forces (living units), separate from Training progress.
    units: Dict[str, int] = field(default_factory=dict)
    # Ready building counts kept for simple scripts; detailed view is in `building`.
    buildings: Dict[str, int] = field(default_factory=dict)
    building: Dict[str, dict[str, Any]] = field(default_factory=dict)
    training: Dict[str, dict[str, Any]] = field(default_factory=dict)
    research: Dict[str, str] = field(default_factory=dict)
    combat: Dict[str, dict[str, Any]] = field(default_factory=dict)
    scouting: Dict[str, dict[str, Any]] = field(default_factory=dict)
    upgrades: List[str] = field(default_factory=list)
    # Object-level structure ids for upgrade / future targeted actions.
    structures: List[dict[str, Any]] = field(default_factory=list)
    base_count: int = 0
    zones: List[str] = field(default_factory=list)
    orbital_count: Optional[int] = 0
    scan_ready: Optional[int] = 0
    mule_ready: Optional[int] = 0
    recent_events: List[dict[str, Any]] = field(default_factory=list)
    terminated: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Canonical external schema; legacy attributes stay Python-only.

        Counts and identifiers used by the agent occur in their semantic
        section once.  The compatibility aliases on this dataclass remain for
        existing scripts, but are not copied into JSON, context, or records.
        """
        return {
            "game": self.game.to_dict(),
            "economy": self.economy.to_dict(),
            "map_control": self.map_control.to_dict(),
            "map_topology": dict(self.map_topology),
            "zone_state": list(self.zone_state),
            "production_priority": list(self.production_priority),
            "production": None if self.production is None else list(self.production),
            "building": dict(self.building),
            "training": dict(self.training),
            "own_forces": self.own_forces.to_dict(),
            "research": dict(self.research),
            "structures": list(self.structures),
            "abilities": dict(self.abilities),
            "combat": dict(self.combat),
            "scouting": dict(self.scouting),
            "recent_events": list(self.recent_events),
            "terminated": self.terminated,
        }

    def section_lines(self) -> List[str]:
        """Complete semantic view, identical to the platform context renderer."""
        return render_observation_text(self.to_dict()).splitlines()


def split_own_forces(
    units: Dict[str, int],
    *,
    assigned: Optional[Dict[str, int]] = None,
) -> OwnForcesView:
    workers: Dict[str, int] = {}
    army: Dict[str, int] = {}
    for name, count in units.items():
        amount = int(count)
        if amount <= 0:
            continue
        if name in WORKER_UNIT_NAMES:
            workers[name] = amount
        else:
            army[name] = amount
    assigned_map = {
        str(name): min(int(count), int(army.get(str(name), 0)))
        for name, count in dict(assigned or {}).items()
        if int(count) > 0 and str(name) in army
    }
    free_map: Dict[str, int] = {}
    for name, total in army.items():
        free_map[name] = max(0, int(total) - int(assigned_map.get(name, 0)))
    for name, count in assigned_map.items():
        free_map.setdefault(name, 0)
        _ = count
    return OwnForcesView(
        workers=workers,
        army=army,
        assigned=assigned_map,
        free={name: count for name, count in free_map.items() if count > 0},
    )
