"""Deterministic FakeBackend for contract tests (no SC2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from sc2bench_env.backends.base import Backend, BackendSnapshot
from sc2bench_env.interface.action_catalog import cost_table, prerequisite_table, get_target
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.runtime.scheduler import DecisionTrigger, trigger_satisfied
from sc2bench_env.runtime.task import Demand, DemandState
from sc2bench_env.runtime.task_manager import DemandUpdate

PREREQUISITES: Dict[str, List[str]] = prerequisite_table(race="terran")
COSTS: Dict[str, Dict[str, int]] = cost_table(race="terran")

FAKE_ZONE_COUNT = 16
SCAN_ENERGY_COST = float(COSTS["scan"]["energy"])
TOWNHALL_KEYS = ("command_center", "orbital_command", "planetary_fortress")


def _linear_topology(count: int = FAKE_ZONE_COUNT, hop: float = 20.0) -> Dict[str, object]:
    rows = []
    for index in range(count):
        neighbors = []
        if index > 0:
            neighbors.append({"zone_id": f"zone_{index - 1}", "path_distance": hop})
        if index + 1 < count:
            neighbors.append({"zone_id": f"zone_{index + 1}", "path_distance": hop})
        rows.append({
            "zone_id": f"zone_{index}",
            "has_ramp": False,
            "path_distance_from_own_main": hop * index,
            "path_distance_to_enemy_main": hop * (count - 1 - index),
            "corridor_neighbors": neighbors,
        })
    return {
        "distance_basis": "synthetic_linear",
        "neighbor_basis": "synthetic_linear",
        "verified_path_pair_count": max(0, count - 1),
        "total_path_pair_count": max(0, count - 1),
        "zones": rows,
    }


@dataclass
class _WorkItem:
    demand_id: str
    action: str
    target: str
    remaining_seconds: float
    phase: str = "waiting"  # waiting | en_route | constructing | producing | researching | ability | combat
    reserved_minerals: int = 0
    reserved_vespene: int = 0
    reserved_supply: int = 0
    action_reported: bool = False  # build/research action completion already emitted
    to: Optional[str] = None
    route: Optional[tuple[str, ...]] = None
    style: Optional[str] = None
    units: Optional[Dict[str, int]] = None
    alive_units: Optional[Dict[str, int]] = None
    order_index: int = 0
    # Internal virtual producer/channel; never exposed in Agent Observation.
    production_slot: Optional[tuple[str, int, int]] = None
    withdrawing: bool = False


@dataclass
class FakeBackend(Backend):
    """Simulates dispatch, construction start, production, and cancel boundaries."""

    minerals: int = 50
    vespene: int = 0
    supply_used: int = 12
    supply_cap: int = 15
    units: Dict[str, int] = field(default_factory=lambda: {"scv": 12})
    buildings: Dict[str, int] = field(default_factory=lambda: {"command_center": 1})
    under_construction: Dict[str, int] = field(default_factory=dict)
    upgrades: Set[str] = field(default_factory=set)
    in_progress_research: Set[str] = field(default_factory=set)
    # Stable object ids for townhalls: id -> type name
    structure_types: Dict[str, str] = field(default_factory=lambda: {"cc_0": "command_center"})
    orbital_energy: float = 0.0
    game_time_seconds: float = 0.0
    mineral_income_per_second: float = 8.0
    vespene_income_per_second: float = 0.0
    game_time_limit_seconds: Optional[float] = 600.0
    terminated: bool = False
    result: Optional[str] = None

    _active_ids: Set[str] = field(default_factory=set)
    _queue: List[_WorkItem] = field(default_factory=list)
    _updates: List[DemandUpdate] = field(default_factory=list)
    _known: Dict[str, Demand] = field(default_factory=dict)
    _config: Optional[EpisodeConfig] = None
    _race: str = field(default="terran", init=False)
    _worker_name: str = field(default="scv", init=False)
    _townhall_name: str = field(default="command_center", init=False)
    _townhall_keys: tuple = field(
        default=("command_center", "orbital_command", "planetary_fortress"), init=False,
    )
    _supply_name: str = field(default="supply_depot", init=False)
    _gas_name: str = field(default="refinery", init=False)
    _costs: Dict[str, Dict[str, int]] = field(default_factory=lambda: cost_table(race="terran"), init=False)
    _prerequisites: Dict[str, List[str]] = field(default_factory=lambda: prerequisite_table(race="terran"), init=False)
    _producer_addons: Dict[tuple[str, int], Optional[str]] = field(default_factory=dict)
    end_reason: Optional[str] = None

    def start_episode(self, config: EpisodeConfig) -> BackendSnapshot:
        from sc2bench_env.interface.races import require_supported_own_race
        require_supported_own_race(config.race)
        costs = cost_table(race=config.race)
        prerequisites = prerequisite_table(race=config.race)
        self._race = config.race
        self._costs = costs
        self._prerequisites = prerequisites
        if config.race == "protoss":
            self._worker_name = "probe"
            self._townhall_name = "nexus"
            self._townhall_keys = ("nexus",)
            self._supply_name = "pylon"
            self._gas_name = "assimilator"
            opening_units = {"probe": 12}
            opening_buildings = {"nexus": 1}
            opening_structures = {"cc_0": "nexus"}
        elif config.race == "zerg":
            self._worker_name = "drone"
            self._townhall_name = "hatchery"
            self._townhall_keys = ("hatchery", "lair", "hive")
            self._supply_name = "overlord"
            self._gas_name = "extractor"
            opening_units = {"drone": 12, "overlord": 1}
            opening_buildings = {"hatchery": 1}
            opening_structures = {"cc_0": "hatchery"}
        else:
            self._worker_name = "scv"
            self._townhall_name = "command_center"
            self._townhall_keys = TOWNHALL_KEYS
            self._supply_name = "supply_depot"
            self._gas_name = "refinery"
            opening_units = {"scv": 12}
            opening_buildings = {"command_center": 1}
            opening_structures = {"cc_0": "command_center"}
        self._config = config
        self.game_time_seconds = 0.0
        self.terminated = False
        self.result = None
        self.end_reason = None
        self.game_time_limit_seconds = config.game_time_limit_seconds
        self.minerals = 50
        self.vespene = 0
        self.supply_used = 12
        from sc2bench_env.data.knowledge import food_provided, opening_supply_cap

        self.supply_cap = opening_supply_cap(config.race)
        self.units = opening_units
        self.buildings = opening_buildings
        self.under_construction = {}
        self.upgrades = set()
        self.in_progress_research = set()
        self.structure_types = opening_structures
        self.orbital_energy = 0.0
        self._active_ids.clear()
        self._queue.clear()
        self._updates.clear()
        self._known.clear()
        self._producer_addons.clear()
        return self.snapshot()

    def submit(self, tasks: List[Demand]) -> None:
        active_ids = {demand.demand_id for demand in tasks if demand.is_active}
        self._active_ids = set(active_ids)
        self._known = {demand.demand_id: demand for demand in tasks if demand.is_active}
        # Drop cancelled/finished work that is no longer active.
        self._queue = [
            item
            for item in self._queue
            if item.demand_id in active_ids
            or item.phase in {"constructing", "researching"}
        ]

        for demand in tasks:
            if not demand.is_active:
                continue
            existing_items = [item for item in self._queue if item.demand_id == demand.demand_id]
            producing = [item for item in existing_items if item.phase == "producing"]
            waiting = [item for item in existing_items if item.phase == "waiting"]
            # After cancel trims demand.count, drop surplus waiting work items.
            allowed_unfinished = max(0, int(demand.count) - int(demand.produced))
            keep_waiting = max(0, allowed_unfinished - len(producing))
            if len(waiting) > keep_waiting:
                drop_ids = {id(item) for item in waiting[keep_waiting:]}
                self._queue = [item for item in self._queue if id(item) not in drop_ids]
                waiting = waiting[:keep_waiting]
            existing = len(producing) + len(waiting)
            needed = max(0, demand.count - demand.produced - existing)
            if demand.action == "combat":
                for item in existing_items:
                    item.style, item.target = demand.style, demand.target
                    if item.withdrawing != demand.withdrawing:
                        item.withdrawing = demand.withdrawing
                        item.remaining_seconds = 3.0 if item.withdrawing else 1e9
                # Combat binds immediately at accept; keep one long-lived work item.
                if not existing_items:
                    self._queue.append(
                        _WorkItem(
                            demand_id=demand.demand_id,
                            action=demand.action,
                            target=demand.target,
                            remaining_seconds=3.0 if demand.withdrawing else 1e9,
                            phase="combat",
                            to=demand.to,
                            route=demand.route,
                            style=demand.style,
                            units=dict(demand.units or {}),
                            alive_units=dict(demand.units or {}),
                            order_index=int(demand.order_index),
                            action_reported=True,
                            withdrawing=demand.withdrawing,
                        )
                    )
                continue
            cost_key = self._cost_key(demand.action, demand.target, demand.to)
            route = demand.route
            if demand.action == "scout" and route == "all":
                route = tuple(row["zone_id"] for row in self.snapshot().info["zone_state"]
                              if row["known_owner"] != "self")
            for _ in range(needed):
                self._queue.append(
                    _WorkItem(
                        demand_id=demand.demand_id,
                        action=demand.action,
                        target=demand.target,
                        remaining_seconds=float(self._costs.get(cost_key, {}).get("build_time", 10)),
                        phase="waiting",
                        to=demand.to,
                        route=route,
                        order_index=int(demand.order_index),
                    )
                )
        # Keep spend priority aligned with demand order_index.
        self._queue.sort(key=lambda item: (item.order_index, item.demand_id))

    def run_until(self, trigger: DecisionTrigger) -> bool:
        if self.terminated:
            return True

        wait_started_at = self.game_time_seconds
        while not self.terminated:
            if trigger_satisfied(
                trigger,
                snapshot=self.snapshot(),
                wait_started_at=wait_started_at,
            ):
                break
            self._tick(1.0)

        if (
            trigger.max_game_time_seconds is not None
            and self.game_time_seconds >= trigger.max_game_time_seconds
        ):
            self.terminated = True
            self.result = "Result.Tie"
            self.end_reason = "time_limit"
        return self.terminated

    def collect_updates(self) -> List[DemandUpdate]:
        # Refresh waiting reasons for still-waiting items.
        occupied_slots = self._occupied_production_slots()
        for item in self._queue:
            if item.phase != "waiting":
                continue
            waiting_for = self._waiting_reason(item)
            if waiting_for is None and self._uses_shared_producer(item):
                if self._available_production_slot(item, occupied_slots) is None:
                    waiting_for = "production_capacity"
            self._updates.append(
                DemandUpdate(
                    demand_id=item.demand_id,
                    action=item.action,
                    target=item.target,
                    state=DemandState.WAITING_TO_START,
                    waiting_for=waiting_for,
                )
            )
        # Emit final per-demand queue occupancy after intermediate tick events.
        # Cancellation must preserve ALL paid slots, not an assumed single unit.
        for demand in self._known.values():
            if demand.action != "train":
                continue
            producing = sum(1 for item in self._queue
                            if item.demand_id == demand.demand_id and item.phase == "producing")
            self._updates.append(DemandUpdate(
                demand_id=demand.demand_id, action="train", target=demand.target,
                state=DemandState.IN_PRODUCTION if producing else DemandState.WAITING_TO_START,
                in_progress=producing,
            ))
        updates = list(self._updates)
        self._updates.clear()
        return updates

    def snapshot(self) -> BackendSnapshot:
        base_count = sum(int(self.buildings.get(key, 0)) for key in self._townhall_keys)
        orbital_count = int(self.buildings.get("orbital_command", 0))
        scan_cost = self._costs.get("scan")
        scan_ready = 1 if scan_cost and orbital_count > 0 and self.orbital_energy >= float(scan_cost["energy"]) else 0
        structures = [
            {"id": object_id, "type": type_name}
            for object_id, type_name in sorted(self.structure_types.items())
        ]
        in_production = {
            target: sum(
                1
                for item in self._queue
                if item.action == "train" and item.target == target and item.phase == "producing"
            )
            for target in {item.target for item in self._queue if item.action == "train"}
        }
        worker_count = int(self.units.get(self._worker_name, 0))
        refineries = int(self.buildings.get(self._gas_name, 0))
        ideal_worker_count = 16 * max(1, base_count) + 3 * refineries
        known_enemy = 1
        own_indices = list(range(min(FAKE_ZONE_COUNT - known_enemy, max(0, base_count))))
        enemy_index = FAKE_ZONE_COUNT - 1 if FAKE_ZONE_COUNT > 1 else None
        zone_state = []
        base_resources = []
        for index in range(FAKE_ZONE_COUNT):
            zone_id = f"zone_{index}"
            if index in own_indices and index != enemy_index:
                known_owner = "self"
            elif enemy_index is not None and index == enemy_index:
                known_owner = "enemy"
            else:
                known_owner = "unconfirmed"
            roles = {0: "own_main", 1: "own_natural",
                     FAKE_ZONE_COUNT - 2: "enemy_natural", FAKE_ZONE_COUNT - 1: "enemy_main"}
            row: Dict[str, object] = {
                "zone_id": zone_id,
                "zone_role": roles.get(index, "other_expansion"),
                "known_owner": known_owner,
                "vision_state": "visible" if known_owner == "self" else "fogged",
                # Synthetic contract fixture: all own entities are placed at
                # own_main; FakeBackend does not simulate enemy scouting.
                "own_contents": {
                    "units": ({name: int(count) for name, count in sorted(self.units.items())
                               if int(count) > 0} if index == 0 else {}),
                    "buildings": ({name: int(count)
                                   for name, count in sorted(self.buildings.items())
                                   if int(count) > 0} if index == 0 else {}),
                },
                "visible_enemy_contents": {"units": {}, "buildings": {}},
                "last_seen_enemy_contents": {"units": {}, "buildings": {}},
                "enemy_information_age_seconds": None,
                "visible_enemy_weapon_in_range": False,
            }
            zone_state.append(row)
            # Declared synthetic fixture, not simulated depletion or scouting.
            owned_gas = min(2, refineries) if index == 0 else 0
            base_resources.append({"zone_id": zone_id,
                                   "minerals_remaining": 10500 if known_owner == "self" else None,
                                   "minerals_initial": 10500,
                                   "vespene_remaining": 4500 if known_owner == "self" else None,
                                   "vespene_initial": 4500, "geyser_slots": 2,
                                   "owned_gas_structure_count": owned_gas,
                                   "available_geyser_slots": 2 - owned_gas if known_owner == "self" else None,
                                   "resource_visibility": "visible" if known_owner == "self" else "unknown"})
        scout_progress = None
        for item in self._queue:
            if (
                item.action == "scout"
                and item.phase == "ability"
                and item.demand_id in self._active_ids
            ):
                route = [str(zone) for zone in (item.route or ())]
                total = max(1.0, float(self._costs.get("scout", {}).get("build_time", 8))) * max(1, len(route))
                elapsed = max(0.0, total - float(item.remaining_seconds))
                per_hop = total / max(1, len(route))
                index = min(len(route) - 1, int(elapsed // per_hop)) if route else 0
                scout_progress = {
                    "route": route,
                    "moving_to": route[index] if route else None,
                    "waypoint_index": index,
                    "assigned": True,
                    "done": False,
                }
                break
        info: Dict[str, object] = {
            "backend": "fake",
            "base_count": base_count,
            "upgrades": sorted(self.upgrades),
            "zones": [f"zone_{index}" for index in range(FAKE_ZONE_COUNT)],
            "zones_under_attack": [],
            "zone_state": zone_state,
            "map_topology": _linear_topology(),
            "base_resources": base_resources,
            "structures": structures,
            "orbital_count": orbital_count,
            "orbital_energies": [round(self.orbital_energy, 1)] if orbital_count else [],
            "scan_ready": scan_ready,
            "mule_ready": scan_ready,
            **({
                "nexus_count": int(self.buildings.get("nexus", 0)),
                "nexus_energies": [round(self.orbital_energy, 1)] if int(self.buildings.get("nexus", 0)) else [],
                "chrono_ready": 1 if int(self.buildings.get("nexus", 0)) and self.orbital_energy >= 50 else 0,
            } if self._race == "protoss" else {}),
            **({
                "queen_count": int(self.units.get("queen", 0)),
                "queen_energies": [round(self.orbital_energy, 1)] if int(self.units.get("queen", 0)) else [],
                "inject_ready": 1 if int(self.units.get("queen", 0)) and self.orbital_energy >= 25 else 0,
            } if self._race == "zerg" else {}),
            "under_construction": dict(self.under_construction),
            "in_production_units": {key: value for key, value in in_production.items() if value},
            "in_progress_research": sorted(self.in_progress_research),
            "supply_workers": worker_count,
            "supply_army": max(0, int(self.supply_used) - worker_count),
            "ideal_worker_count": ideal_worker_count,
            "mineral_income_per_minute": round(self.mineral_income_per_second * 60.0, 1),
            "vespene_income_per_minute": round(self.vespene_income_per_second * 60.0, 1),
            "known_enemy_base_count": known_enemy,
            "own_base_count": min(base_count, FAKE_ZONE_COUNT - known_enemy),
            "unconfirmed_expansion_count": max(0, FAKE_ZONE_COUNT - base_count - known_enemy),
        }
        if scout_progress is not None:
            info["scout_progress"] = scout_progress
        self._refresh_combat_alive()
        combat_progress: Dict[str, Dict[str, object]] = {}
        for item in self._queue:
            if item.action != "combat" or item.phase != "combat":
                continue
            if item.demand_id not in self._active_ids:
                continue
            requested = dict(item.units or {})
            alive = dict(item.alive_units or {})
            combat_progress[item.demand_id] = {
                "style": item.style,
                "target": item.target,
                "requested": requested,
                "alive": alive,
                "assigned": True,
                "phase": "withdrawing" if item.withdrawing else "executing",
                "visible_enemy_nearby": None,
                "weapon_cooldown_active_count": None,
            }
        if combat_progress:
            info["combat_progress"] = combat_progress
        return BackendSnapshot(
            game_time_seconds=self.game_time_seconds,
            minerals=int(self.minerals),
            vespene=int(self.vespene),
            supply_used=int(self.supply_used),
            supply_cap=int(self.supply_cap),
            units=dict(self.units),
            buildings=dict(self.buildings),
            terminated=self.terminated,
            result=self.result,
            end_reason=self.end_reason,
            info=info,
        )

    def close_episode(self) -> None:
        self._queue.clear()
        self._active_ids.clear()
        self._known.clear()
        self._producer_addons.clear()

    def _tick(self, dt: float) -> None:
        self.game_time_seconds += dt
        self.minerals += self.mineral_income_per_second * dt
        self.vespene += self.vespene_income_per_second * dt
        if self._race == "protoss" and int(self.buildings.get("nexus", 0)) > 0:
            self.orbital_energy = min(200.0, self.orbital_energy + 0.6 * dt)
        elif self._race == "zerg" and int(self.units.get("queen", 0)) > 0:
            self.orbital_energy = min(200.0, self.orbital_energy + 0.6 * dt)
        elif int(self.buildings.get("orbital_command", 0)) > 0:
            self.orbital_energy = min(200.0, self.orbital_energy + 0.6 * dt)
        self._try_start_work()
        self._advance_work(dt)

    def _townhalls(self) -> int:
        return sum(int(self.buildings.get(key, 0)) for key in self._townhall_keys)

    def _has_prereqs(self, target: str) -> bool:
        for req in self._prerequisites.get(target, []):
            owned = self._prerequisite_count(req)
            if owned <= 0:
                return False
        return True

    def _prerequisite_count(self, target: str) -> int:
        if target == "command_center":
            return self._townhalls()
        if self._race == "zerg" and target == "hatchery":
            return self._townhalls()
        if self._race == "zerg" and target == "spire":
            return int(self.buildings.get("spire", 0)) + int(self.buildings.get("greater_spire", 0))
        if self._race == "zerg" and target == "hydralisk_den":
            return int(self.buildings.get("hydralisk_den", 0)) + int(self.buildings.get("lurker_den", 0))
        if target.endswith(("_techlab", "_reactor")):
            parent, addon = target.rsplit("_", 1)
            return self._sync_producer_addons(parent).count(addon)
        return self.buildings.get(target, 0) + self.units.get(target, 0) + int(target in self.upgrades)

    def _cost_key(self, action: str, target: str, to: Optional[str] = None) -> str:
        if action == "scan":
            return "scan"
        if action in {"call_mule", "chrono_boost", "inject_larva", "spawn_creep_tumor"}:
            return action
        if action == "scout":
            return "scout"
        if action == "combat":
            # Style name is the catalog key; target is the zone id.
            return target if target in self._costs else "attack"
        if action == "upgrade":
            return to or "orbital_command"
        return target

    def _waiting_reason(self, item: _WorkItem) -> Optional[str]:
        cost_key = self._cost_key(item.action, item.target, item.to)
        cost = self._costs.get(cost_key)
        if cost is None:
            return "unknown_target"
        if item.action in {"scan", "call_mule"}:
            if int(self.buildings.get("orbital_command", 0)) <= 0:
                return "prerequisite:orbital_command"
            if self.orbital_energy < float(cost["energy"]):
                return "resources"
            return None
        if item.action == "chrono_boost":
            if int(self.buildings.get("nexus", 0)) <= 0:
                return "prerequisite:nexus"
            if self.orbital_energy < float(cost["energy"]):
                return "resources"
            return None
        if item.action in {"inject_larva", "spawn_creep_tumor"}:
            if int(self.units.get("queen", 0)) <= 0:
                return "prerequisite:queen"
            if self.orbital_energy < float(cost["energy"]):
                return "resources"
            return None
        if item.action == "scout":
            if int(self.units.get(self._worker_name, 0)) <= 0:
                return f"prerequisite:{self._worker_name}"
            return None
        if item.action == "upgrade":
            if item.target not in self.structure_types:
                return f"unknown_structure:{item.target}"
            morph_source = {
                "orbital_command": "command_center",
                "planetary_fortress": "command_center",
                "lair": "hatchery",
                "hive": "lair",
            }.get(item.to, "command_center")
            if self.structure_types.get(item.target) != morph_source:
                return "not_command_center" if morph_source == "command_center" else "wrong_structure"
            if item.to == "orbital_command" and not self._has_prereqs("orbital_command"):
                return "prerequisite:barracks"
            if item.to == "planetary_fortress" and not self._has_prereqs("planetary_fortress"):
                return "prerequisite:engineering_bay"
            if item.to in {"lair", "hive"} and not self._has_prereqs(item.to):
                missing = next(
                    req for req in self._prerequisites.get(item.to, [])
                    if self._prerequisite_count(req) <= 0
                )
                return f"prerequisite:{missing}"
            if self.minerals < cost["minerals"] or self.vespene < cost["vespene"]:
                return "resources"
            return None
        if item.action != "scan" and not self._has_prereqs(item.target):
            missing = next(
                (
                    req
                    for req in self._prerequisites.get(item.target, [])
                    if self._prerequisite_count(req) <= 0
                ),
                "prerequisite",
            )
            return f"prerequisite:{missing}"
        if item.action == "build" and item.target == "orbital_command":
            if int(self.buildings.get("command_center", 0)) <= 0:
                return "prerequisite:command_center"
        if self.minerals < cost["minerals"] or self.vespene < cost["vespene"]:
            return "resources"
        if item.action == "train" and self.supply_used + cost["supply"] > self.supply_cap:
            return "supply"
        return None

    def _production_slots(self, action: str, target: str) -> int:
        if target == "orbital_command":
            return max(0, int(self.buildings.get("command_center", 0)))
        if action in {"scan", "call_mule", "chrono_boost", "inject_larva", "spawn_creep_tumor", "upgrade", "scout", "combat"}:
            return 1
        return max(1, int(self.units.get(self._worker_name, 0)))

    def _sync_producer_addons(self, parent: str) -> List[Optional[str]]:
        """Preserve virtual parent identity as aggregate building counts grow.

        Fake has no SC2 tags/placement. This deterministic attachment layout
        models capacity only, not lift/land, destroyed producers or swapping.
        """
        count = max(0, int(self.buildings.get(parent, 0)))
        layout = [self._producer_addons.get((parent, index)) for index in range(count)]
        for addon in ("techlab", "reactor"):
            desired = max(0, int(self.buildings.get(f"{parent}_{addon}", 0)))
            assigned = [i for i, current in enumerate(layout) if current == addon]
            for index in assigned[desired:]:
                layout[index] = None
        for addon in ("techlab", "reactor"):
            missing = max(0, int(self.buildings.get(f"{parent}_{addon}", 0)) - layout.count(addon))
            for index, current in enumerate(layout):
                if missing and current is None:
                    layout[index] = addon
                    missing -= 1
        for index, addon in enumerate(layout):
            self._producer_addons[(parent, index)] = addon
        return layout

    def _uses_shared_producer(self, item: _WorkItem) -> bool:
        spec = get_target(item.target, race=self._race)
        return item.action in {"train", "research"} or (
            item.action == "build" and spec is not None and spec.kind == "addon"
        )

    def _production_slot_options(self, item: _WorkItem) -> List[tuple[str, int, int]]:
        spec = get_target(item.target, race=self._race)
        if spec is None:
            return []
        producer = spec.produced_at
        if item.action == "research":
            if producer.endswith("_techlab"):
                parent = producer.rsplit("_", 1)[0]
                indices = [i for i, addon in enumerate(self._sync_producer_addons(parent)) if addon == "techlab"]
            else:
                indices = range(max(0, int(self.buildings.get(producer, 0))))
            return [(f"research:{producer}", index, 0) for index in indices]
        if producer == "command_center":
            return [("train:command_center", index, 0) for index in range(self._townhalls())]
        layout = self._sync_producer_addons(producer)
        if item.action == "build":
            # Add-on construction requires an idle, bare parent and blocks it.
            return [(f"train:{producer}", index, -1) for index, addon in enumerate(layout) if addon is None]
        needs_lab = any(req.endswith("_techlab") for req in spec.prerequisites)
        # Ordinary units prefer Reactors/bare parents, leaving labs available.
        indices = sorted(range(len(layout)), key=lambda i: {"reactor": 0, None: 1, "techlab": 2}[layout[i]])
        return [(f"train:{producer}", index, channel) for index in indices
                if not needs_lab or layout[index] == "techlab"
                for channel in range(2 if layout[index] == "reactor" else 1)]

    def _occupied_production_slots(self) -> Set[tuple[str, int, int]]:
        return {work.production_slot for work in self._queue
                if work.production_slot is not None and work.phase != "waiting"}

    def _available_production_slot(
        self, item: _WorkItem, occupied: Optional[Set[tuple[str, int, int]]] = None
    ) -> Optional[tuple[str, int, int]]:
        if occupied is None:
            occupied = self._occupied_production_slots()
        for slot in self._production_slot_options(item):
            pool, index, channel = slot
            if slot in occupied:
                continue
            if (pool, index, -1) in occupied:
                continue
            if channel == -1 and any(other[:2] == slot[:2] for other in occupied):
                continue
            return slot
        return None

    def _try_start_work(self) -> None:
        """Start work in order_index priority with soft resource reservation.

        Earlier demands get first claim on minerals/vespene/supply/energy;
        leftovers may fund later demands. Prerequisite-blocked earlier demands
        do not reserve resources, so later ready work can run.
        """
        occupied_slots = self._occupied_production_slots()
        other_started_by_key: Dict[tuple[str, str], int] = {}
        for item in self._queue:
            if item.phase not in {"waiting", "en_route"} and not self._uses_shared_producer(item):
                key = (item.action, item.target)
                other_started_by_key[key] = other_started_by_key.get(key, 0) + 1

        budget_minerals = float(self.minerals)
        budget_vespene = float(self.vespene)
        budget_supply = max(0, int(self.supply_cap) - int(self.supply_used))
        budget_energy = float(self.orbital_energy)

        for item in self._queue:
            if item.phase != "waiting":
                continue
            cost_key = self._cost_key(item.action, item.target, item.to)
            cost = self._costs.get(cost_key)
            if cost is None:
                self._fail(item, f"unknown_target:{item.target}")
                continue
            if item.action == "scan":
                try:
                    zone_index = int(item.target.split("_", 1)[1])
                except (IndexError, ValueError):
                    self._fail(item, f"invalid_zone:{item.target}")
                    continue
                if zone_index < 0 or zone_index >= FAKE_ZONE_COUNT:
                    self._fail(item, f"invalid_zone:{item.target}")
                    continue
            if item.action == "scout":
                invalid_zone = None
                for zone in item.route or ():
                    try:
                        zone_index = int(zone.split("_", 1)[1])
                    except (IndexError, ValueError):
                        invalid_zone = zone
                        break
                    if zone_index < 0 or zone_index >= FAKE_ZONE_COUNT:
                        invalid_zone = zone
                        break
                if invalid_zone is not None:
                    self._fail(item, f"invalid_zone:{invalid_zone}")
                    continue
            if item.action == "research" and item.target in self.upgrades:
                self._fail(item, f"already_researched:{item.target}")
                continue

            reason = self._waiting_reason(item)
            if reason is not None and not str(reason).startswith("resources") and reason != "supply":
                # Prerequisite / producer missing: do not reserve.
                continue

            need_m = float(cost["minerals"])
            need_v = float(cost["vespene"])
            need_s = int(cost["supply"]) if item.action == "train" else 0
            need_e = float(cost["energy"])
            can_fund = (
                need_m <= budget_minerals
                and need_v <= budget_vespene
                and need_s <= budget_supply
                and need_e <= budget_energy
            )
            if not can_fund:
                # Soft-reserve toward this higher-priority demand.
                budget_minerals = max(0.0, budget_minerals - need_m)
                budget_vespene = max(0.0, budget_vespene - need_v)
                budget_supply = max(0, budget_supply - need_s)
                budget_energy = max(0.0, budget_energy - need_e)
                continue

            if reason is not None:
                continue

            key = (item.action, item.target)
            production_slot = None
            if self._uses_shared_producer(item):
                production_slot = self._available_production_slot(item, occupied_slots)
                if production_slot is None:
                    continue
            elif other_started_by_key.get(key, 0) >= self._production_slots(item.action, item.target):
                continue
            item.production_slot = production_slot

            if item.action in {"scan", "call_mule", "chrono_boost", "inject_larva", "spawn_creep_tumor"}:
                self.orbital_energy -= need_e
                budget_energy -= need_e
            elif item.action not in {"scout"}:
                self.minerals -= cost["minerals"]
                self.vespene -= cost["vespene"]
                budget_minerals -= need_m
                budget_vespene -= need_v
            item.reserved_minerals = cost["minerals"]
            item.reserved_vespene = cost["vespene"]
            item.reserved_supply = cost["supply"]
            if item.action == "train":
                self.supply_used += cost["supply"]
                budget_supply -= need_s

            if item.action == "build":
                item.phase = "en_route"
                item.remaining_seconds = 2.0
                self._updates.append(
                    DemandUpdate(
                        demand_id=item.demand_id,
                        action=item.action,
                        target=item.target,
                        state=DemandState.WORKER_EN_ROUTE,
                    )
                )
            elif item.action == "train":
                item.phase = "producing"
                item.remaining_seconds = float(cost["build_time"])
                self._updates.append(
                    DemandUpdate(
                        demand_id=item.demand_id,
                        action=item.action,
                        target=item.target,
                        state=DemandState.IN_PRODUCTION,
                    )
                )
            elif item.action == "research":
                item.phase = "researching"
                item.remaining_seconds = float(cost["build_time"])
                self.in_progress_research.add(item.target)
                self._updates.append(
                    DemandUpdate(
                        demand_id=item.demand_id,
                        action=item.action,
                        target=item.target,
                        produced_delta=1,
                        state=DemandState.COMPLETED,
                    )
                )
                item.action_reported = True
            elif item.action == "upgrade":
                item.phase = "constructing"
                item.remaining_seconds = float(cost["build_time"])
                self.under_construction[item.to or "orbital_command"] = (
                    self.under_construction.get(item.to or "orbital_command", 0) + 1
                )
                self._updates.append(
                    DemandUpdate(
                        demand_id=item.demand_id,
                        action=item.action,
                        target=item.target,
                        produced_delta=1,
                        state=DemandState.COMPLETED,
                    )
                )
                item.action_reported = True
            elif item.action in {"scan", "call_mule", "chrono_boost", "inject_larva", "spawn_creep_tumor", "scout"}:
                item.phase = "ability"
                if item.action == "scout":
                    hops = max(1, len(item.route or ()))
                    item.remaining_seconds = float(cost["build_time"]) * hops
                else:
                    item.remaining_seconds = float(cost["build_time"])
            if production_slot is not None:
                occupied_slots.add(production_slot)
            else:
                other_started_by_key[key] = other_started_by_key.get(key, 0) + 1

    def _refresh_combat_alive(self) -> None:
        # Fake has no per-unit battle simulation. Attribute aggregate losses
        # deterministically to later missions after free units, never replenish
        # an existing mission merely because new units were produced.
        available = dict(self.units)
        for item in sorted(self._queue, key=lambda item: item.order_index):
            if item.action != "combat" or item.phase != "combat":
                continue
            previous = item.alive_units if item.alive_units is not None else (item.units or {})
            item.alive_units = {}
            for name, count in previous.items():
                alive = min(int(count), max(0, int(available.get(name, 0))))
                item.alive_units[name] = alive
                available[name] = max(0, int(available.get(name, 0)) - alive)

    def _advance_work(self, dt: float) -> None:
        self._refresh_combat_alive()
        remaining: List[_WorkItem] = []
        for item in self._queue:
            if item.phase == "waiting":
                remaining.append(item)
                continue
            if item.action == "combat" and item.phase == "combat":
                alive_total = sum((item.alive_units or {}).values())
                if item.withdrawing:
                    item.remaining_seconds -= dt
                if item.withdrawing and item.remaining_seconds <= 0 and alive_total > 0:
                    self._updates.append(DemandUpdate(demand_id=item.demand_id,
                                                     state=DemandState.COMPLETED, end_reason="withdrawn"))
                    continue
                if alive_total <= 0 and (item.units or {}):
                    self._updates.append(
                        DemandUpdate(
                            demand_id=item.demand_id,
                            action=item.action,
                            target=item.target,
                            state=DemandState.COMPLETED,
                            end_reason="force_destroyed",
                        )
                    )
                    continue
                remaining.append(item)
                continue
            item.remaining_seconds -= dt
            if item.remaining_seconds > 0:
                remaining.append(item)
                continue

            if item.action == "build" and item.phase == "en_route":
                # Building entity appears: build action completes; world keeps constructing.
                cost = self._costs[item.target]
                item.phase = "constructing"
                item.remaining_seconds = float(cost["build_time"])
                self.under_construction[item.target] = (
                    self.under_construction.get(item.target, 0) + 1
                )
                if not item.action_reported:
                    self._updates.append(
                        DemandUpdate(
                            demand_id=item.demand_id,
                            action=item.action,
                            target=item.target,
                            produced_delta=1,
                            state=DemandState.COMPLETED,
                        )
                    )
                    item.action_reported = True
                remaining.append(item)
                continue

            if item.action == "build" and item.phase == "constructing":
                self.under_construction[item.target] = max(
                    0, self.under_construction.get(item.target, 0) - 1
                )
                if self.under_construction[item.target] == 0:
                    self.under_construction.pop(item.target, None)
                self._finish_building(item.target)
                if item.production_slot is not None:
                    _, index, _ = item.production_slot
                    parent, addon = item.target.rsplit("_", 1)
                    self._producer_addons[(parent, index)] = addon
                continue

            if item.action == "train" and item.phase == "producing":
                self.units[item.target] = self.units.get(item.target, 0) + 1
                if self._race == "zerg" and item.target == "overlord":
                    from sc2bench_env.data.knowledge import food_provided

                    self.supply_cap += food_provided(self._race, "overlord")
                self._updates.append(
                    DemandUpdate(
                        demand_id=item.demand_id,
                        action=item.action,
                        target=item.target,
                        produced_delta=1,
                    )
                )
                continue

            if item.action == "research" and item.phase == "researching":
                self.in_progress_research.discard(item.target)
                self.upgrades.add(item.target)
                continue

            if item.action == "upgrade" and item.phase == "constructing":
                to_type = item.to or "orbital_command"
                self.under_construction[to_type] = max(
                    0, self.under_construction.get(to_type, 0) - 1
                )
                if self.under_construction[to_type] == 0:
                    self.under_construction.pop(to_type, None)
                self._apply_structure_upgrade(item.target, to_type)
                continue

            if item.action in {
                "scan", "call_mule", "chrono_boost", "inject_larva", "spawn_creep_tumor", "scout",
            } and item.phase == "ability":
                self._updates.append(
                    DemandUpdate(
                        demand_id=item.demand_id,
                        action=item.action,
                        target=item.target,
                        produced_delta=1,
                        state=DemandState.COMPLETED,
                    )
                )
                continue

            remaining.append(item)
        self._queue = remaining

    def _apply_structure_upgrade(self, structure_id: str, to_type: str) -> None:
        if structure_id not in self.structure_types:
            return
        previous = self.structure_types[structure_id]
        if previous in self._townhall_keys:
            previous_count = int(self.buildings.get(previous, 0))
            if previous_count > 0:
                self.buildings[previous] = previous_count - 1
                if self.buildings[previous] <= 0:
                    self.buildings.pop(previous, None)
        self.structure_types[structure_id] = to_type
        self.buildings[to_type] = self.buildings.get(to_type, 0) + 1
        if to_type == "orbital_command":
            self.orbital_energy = max(self.orbital_energy, 50.0)

    def _finish_building(self, target: str) -> None:
        if target == "orbital_command":
            cc = int(self.buildings.get("command_center", 0))
            if cc > 0:
                self.buildings["command_center"] = cc - 1
                if self.buildings["command_center"] <= 0:
                    self.buildings.pop("command_center", None)
                # Prefer morphing an existing structure record when present.
                for object_id, type_name in self.structure_types.items():
                    if type_name == "command_center":
                        self.structure_types[object_id] = "orbital_command"
                        break
            self.buildings["orbital_command"] = self.buildings.get("orbital_command", 0) + 1
            self.orbital_energy = max(self.orbital_energy, 50.0)
            return
        self.buildings[target] = self.buildings.get(target, 0) + 1
        from sc2bench_env.data.knowledge import food_provided

        if target == self._supply_name:
            self.supply_cap += food_provided(self._race, target)
        if target == self._townhall_name:
            self.supply_cap += food_provided(self._race, target)
            index = 0
            while f"cc_{index}" in self.structure_types:
                index += 1
            self.structure_types[f"cc_{index}"] = self._townhall_name
        if target == self._gas_name:
            self.vespene_income_per_second = max(self.vespene_income_per_second, 12.0)

    def _fail(self, item: _WorkItem, reason: str) -> None:
        self._updates.append(
            DemandUpdate(
                demand_id=item.demand_id,
                action=item.action,
                target=item.target,
                failure_reason=reason,
                state=DemandState.FAILED,
            )
        )
        self._queue = [entry for entry in self._queue if entry.demand_id != item.demand_id]

    def kill_units(self, target: str, count: int) -> None:
        current = self.units.get(target, 0)
        self.units[target] = max(0, current - count)
        supply = self._costs.get(target, {}).get("supply", 0)
        self.supply_used = max(0, self.supply_used - supply * min(count, current))
