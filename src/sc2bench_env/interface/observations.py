"""Standard Observation returned to external agents."""
from __future__ import annotations


from dataclasses import asdict, dataclass, field
from copy import deepcopy
from typing import Any, Dict, List, Optional

WORKER_UNIT_NAMES = frozenset({"scv", "probe", "drone", "mule"})

OBSERVATION_SECTIONS = (
    ("game", "Game"), ("economy", "Economy"), ("map_control", "Map Control"),
    ("map_topology", "Map Topology"), ("zone_state", "Zone State"),
    ("available_targets", "Tech-Available Targets"),
    ("relevant_zone_ids", "Relevant Zones"),
    ("production_priority", "Production Priority"), ("production", "Production Capacity"), ("building", "Building"),
    ("training", "Training"), ("own_forces", "Own Forces"), ("research", "Research"),
    ("structures", "Structures"), ("abilities", "Abilities"),
    ("combat", "Combat"), ("scouting", "Scouting"), ("recent_events", "Recent Events"),
    ("terminated", "Terminated"),
)

BRIEFING_SECTIONS = tuple(
    item for item in OBSERVATION_SECTIONS if item[0] not in {"map_topology", "relevant_zone_ids"}
)


def _observed_larva_available(rows: Any) -> Any:
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if row.get("resource") == "larva" and row.get("available") is not None:
            return row.get("available")
        if row.get("facility") == "larva":
            return row.get("available", row.get("free_slots", row.get("ready_grounded")))
        if row.get("larva") is not None:
            return row.get("larva")
    return None


def render_observation_text(observation: Dict[str, Any], *, previous: Optional[Dict[str, Any]] = None) -> str:
    """Compact turn briefing for agent context.

    Structured Observation.to_dict() still carries full zone and topology data
    for tools. Text omits the static map table and zones unrelated to current
    bases, enemy contact, army orders or scouting.
    """
    from sc2bench_env.runtime.observation_builder import compact_observation
    from sc2bench_env.interface.observation_text import render_text

    game = observation.get("game") if isinstance(observation, dict) else None
    race = game.get("race") if isinstance(game, dict) else None
    return render_text(
        compact_observation(observation, previous),
        BRIEFING_SECTIONS,
        race=race,
    )


def render_brief_observation_text(
    observation: Dict[str, Any], *, previous: Optional[Dict[str, Any]] = None,
) -> str:
    """Agent-facing projection grouped by decision purpose.

    Full structured Observation remains available to the platform and Read
    tools. This renderer omits empty optional blocks and low-frequency group
    diagnostics while retaining the current economy, macro, army and threats.
    """
    from sc2bench_env.runtime.observation_builder import compact_observation
    from sc2bench_env.interface.observation_text import (
        brief_available_target_lines,
        brief_building_lines,
        brief_combat_lines,
        brief_economy_lines,
        brief_forces_lines,
        brief_priority_lines,
        brief_zone_lines,
        section,
    )

    data = compact_observation(observation, previous)
    game = data.get("game") if isinstance(data.get("game"), dict) else {}
    race = game.get("race") or "terran"
    blocks: List[str] = []

    def useful(lines: List[str]) -> bool:
        return bool(lines and lines != ["none"] and lines != ["unknown"])

    def add(title: str, lines: List[str]) -> None:
        if useful(lines):
            blocks.append(f"[{title}]\n" + "\n".join(lines))

    add("Game", section("game", data.get("game"), race=race))
    add(
        "Economy",
        brief_economy_lines(data.get("economy"), data.get("map_control"), race=race),
    )

    # The Agent submits persistent work rather than assigning individual
    # producer slots. Keep the Brief view centered on that task list; detailed
    # queue/capacity facts remain internal to execution.
    work = brief_priority_lines(data.get("production_priority") or [])
    production_lines: List[str] = ["Ongoing work:", *work]
    if race == "zerg":
        larva_available = _observed_larva_available(data.get("production"))
        if larva_available is not None:
            production_lines.extend(["", f"Larva available: {larva_available}"])
    blocks.append("[Production]\n" + "\n".join(production_lines))

    infrastructure: List[str] = [
        "Buildings:", *brief_building_lines(data.get("building") or {}),
    ]
    for label_text, lines in (
        ("Research", section("research", data.get("research"), race=race)),
        ("Targetable structures", section("structures", data.get("structures"), race=race)),
    ):
        if useful(lines):
            infrastructure.extend(["", f"{label_text}:", *lines])
    blocks.append("[Infrastructure]\n" + "\n".join(infrastructure))

    targets = brief_available_target_lines(data.get("available_targets"))
    blocks.append("[Available Targets]\n" + "\n".join(targets))

    army: List[str] = []
    forces_data = data.get("own_forces") or {}
    forces = brief_forces_lines(forces_data)
    if useful(forces):
        army.extend(forces)
    combat = dict(data.get("combat") or {})
    home = combat.get("group_0")
    free = forces_data.get("free") if isinstance(forces_data, dict) else None
    if isinstance(home, dict) and isinstance(free, dict) and "alive" not in home and free:
        home = dict(home)
        home["alive"] = dict(free)
        combat["group_0"] = home
    groups = brief_combat_lines(combat)
    if useful(groups):
        if army:
            army.append("")
        army.extend(groups)
    scouting = section(
        "scouting", data.get("scouting"), race=race,
        production_priority=data.get("production_priority"),
        buildings=data.get("building"), training=data.get("training"),
    )
    if useful(scouting):
        if army:
            army.append("")
        army.extend(["Scouting:", *scouting])
    add("Army", army)

    zone_lines = brief_zone_lines(data.get("zone_state") or [])
    map_control_data = data.get("map_control") or {}
    if useful(zone_lines) and isinstance(map_control_data, dict):
        zone_total_parts = [
            map_control_data.get("own_base_count"),
            map_control_data.get("known_enemy_base_count"),
            map_control_data.get("unconfirmed_expansion_count"),
        ]
        if all(type(item) is int for item in zone_total_parts):
            omitted = max(0, sum(zone_total_parts) - len(data.get("zone_state") or []))
            if omitted:
                zone_lines.append(f"Other map zones not shown: {omitted}")
    add("Relevant Zones", zone_lines)
    add(
        "Abilities",
        section("abilities", data.get("abilities"), race=race),
    )
    add(
        "Changes",
        section("recent_events", data.get("recent_events"), race=race),
    )
    blocks.append("[Decision]\nStaged actions: none")
    return "\n\n".join(blocks)

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
    status: str = "ongoing"
    result: Optional[str] = None
    end_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "game_time_seconds": self.game_time_seconds,
            "race": self.race,
            "enemy_race": self.enemy_race,
            "status": self.status,
        }
        if self.game_time_limit_seconds is not None:
            payload["game_time_limit_seconds"] = self.game_time_limit_seconds
        if self.seconds_remaining is not None:
            payload["seconds_remaining"] = self.seconds_remaining
        if self.result is not None:
            payload["result"] = self.result
        if self.end_reason is not None:
            payload["end_reason"] = self.end_reason
        return payload


@dataclass
class EconomyView:
    minerals: int = 0
    vespene: int = 0
    supply_used: int = 0
    supply_cap: int = 0
    supply_left: int = 0
    worker_count: int = 0
    workers_on_minerals: Optional[int] = None
    mineral_worker_saturation: Optional[int] = None
    workers_on_vespene: Optional[int] = None
    vespene_worker_saturation: Optional[int] = None
    workers_other: Optional[int] = None
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
    bunker_garrison: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "workers": dict(self.workers),
            "army": dict(self.army),
            "assigned": dict(self.assigned),
            "free": dict(self.free) if self.free or self.assigned else dict(self.army),
        }
        if self.bunker_garrison:
            payload["bunker_garrison"] = dict(self.bunker_garrison)
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
    # Object-level structure ids for morph_townhall and other targeted actions.
    structures: List[dict[str, Any]] = field(default_factory=list)
    base_count: int = 0
    zones: List[str] = field(default_factory=list)
    orbital_count: Optional[int] = 0
    scan_ready: Optional[int] = 0
    mule_ready: Optional[int] = 0
    recent_events: List[dict[str, Any]] = field(default_factory=list)
    available_targets: Dict[str, List[str]] = field(default_factory=dict)
    relevant_zone_ids: List[str] = field(default_factory=list)
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
            "available_targets": {key: list(value) for key, value in self.available_targets.items()},
            "relevant_zone_ids": list(self.relevant_zone_ids),
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

def observation_lines(observation: "Observation") -> List[str]:
    """Complete semantic view, identical to the platform context renderer."""
    return render_observation_text(observation.to_dict()).splitlines()


def agent_observation_view(observation: "Observation") -> "Observation":
    """Return the public structured Brief Observation for an AgentInput.

    The Environment and ToolTurn retain the original full Observation. This
    copy removes the full map topology and low-frequency group diagnostics so
    agents obtain those details through Read tools.
    """
    from sc2bench_env.runtime.observation_builder import compact_observation

    previous = getattr(observation, "text_previous", None)
    compact = compact_observation(observation.to_dict(), previous)
    view = deepcopy(observation)
    view.map_topology = {}
    view.zone_state = list(compact.get("zone_state") or [])
    view.relevant_zone_ids = list(compact.get("relevant_zone_ids") or [])
    # Legacy attributes stay usable without exposing the full map or the prior
    # Full Observation through the public AgentInput object.
    view.zones = list(view.relevant_zone_ids)
    view.text_previous = (
        compact_observation(previous) if isinstance(previous, dict) else None
    )
    map_control = compact.get("map_control")
    if isinstance(map_control, dict):
        view.map_control = MapControlView(**{
            key: map_control.get(key, getattr(view.map_control, key))
            for key in ("own_base_count", "known_enemy_base_count", "unconfirmed_expansion_count", "base_resources")
        })
    # Public structured Brief Observation mirrors the text projection: queue
    # and slot diagnostics stay in the full platform snapshot. Zerg keeps only
    # the immediately relevant larva resource count.
    larva_available = _observed_larva_available(compact.get("production"))
    view.production = (
        [{"resource": "larva", "available": larva_available}]
        if view.game.race == "zerg" and larva_available is not None else []
    )
    if "production_priority" in compact:
        view.production_priority = list(compact.get("production_priority") or [])
    if isinstance(compact.get("combat"), dict):
        view.combat = dict(compact["combat"])
    return view


def split_own_forces(
    units: Dict[str, int],
    *,
    assigned: Optional[Dict[str, int]] = None,
    bunker_garrison: Optional[Dict[str, int]] = None,
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
    garrison = {
        str(name): int(count)
        for name, count in dict(bunker_garrison or {}).items()
        if int(count) > 0
    }
    return OwnForcesView(
        workers=workers,
        army=army,
        assigned=assigned_map,
        free={name: count for name, count in free_map.items() if count > 0},
        bunker_garrison=garrison,
    )
