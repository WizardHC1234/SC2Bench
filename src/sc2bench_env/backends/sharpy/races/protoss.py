"""Protoss Race Adapter: catalog targets -> Sharpy Acts.

Legal target names and kinds come from Action Catalog. This module only
owns the SC2/Sharpy ID bridge (UnitTypeId / UpgradeId) and act wiring.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2
from sharpy.managers.core.grids import ZoneArea
from sharpy.plans import BuildOrder
from sharpy.plans.acts import ActBase, ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.protoss import ProtossUnit
from sharpy.plans.build_step import Step
from sharpy.plans.tactics import DistributeWorkers, PlanCancelBuilding, SpeedMining

from sc2bench_env.backends.sharpy.acts import ActChrono, ActCombatMission, ActScoutRoute
from sc2bench_env.backends.sharpy.defense import PlanZoneDefenseSafe
from sc2bench_env.backends.sharpy.defense_placement import DEFENSE_KINDS, DefensiveGridBuilding
from sc2bench_env.backends.sharpy.gather import PlanHomeGather
from sc2bench_env.backends.sharpy.races.base import RaceAdapter
from sc2bench_env.interface.action_catalog import TargetSpec, get_target, targets_for_action
from sc2bench_env.runtime.task import DemandState, Task

TOWNHALL_TARGETS = frozenset({"nexus"})
GATEWAY_UNITS = frozenset({
    "zealot", "stalker", "sentry", "adept", "high_templar", "dark_templar",
})

_BUILDING_UNIT_IDS: Dict[str, UnitTypeId] = {
    "pylon": UnitTypeId.PYLON,
    "assimilator": UnitTypeId.ASSIMILATOR,
    "gateway": UnitTypeId.GATEWAY,
    "forge": UnitTypeId.FORGE,
    "cybernetics_core": UnitTypeId.CYBERNETICSCORE,
    "photon_cannon": UnitTypeId.PHOTONCANNON,
    "shield_battery": UnitTypeId.SHIELDBATTERY,
    "robotics_facility": UnitTypeId.ROBOTICSFACILITY,
    "robotics_bay": UnitTypeId.ROBOTICSBAY,
    "stargate": UnitTypeId.STARGATE,
    "fleet_beacon": UnitTypeId.FLEETBEACON,
    "twilight_council": UnitTypeId.TWILIGHTCOUNCIL,
    "templar_archives": UnitTypeId.TEMPLARARCHIVE,
    "dark_shrine": UnitTypeId.DARKSHRINE,
    "nexus": UnitTypeId.NEXUS,
}

_UNIT_PRODUCER_IDS: Dict[str, Tuple[UnitTypeId, UnitTypeId]] = {
    "probe": (UnitTypeId.PROBE, UnitTypeId.NEXUS),
    "zealot": (UnitTypeId.ZEALOT, UnitTypeId.GATEWAY),
    "stalker": (UnitTypeId.STALKER, UnitTypeId.GATEWAY),
    "sentry": (UnitTypeId.SENTRY, UnitTypeId.GATEWAY),
    "adept": (UnitTypeId.ADEPT, UnitTypeId.GATEWAY),
    "high_templar": (UnitTypeId.HIGHTEMPLAR, UnitTypeId.GATEWAY),
    "dark_templar": (UnitTypeId.DARKTEMPLAR, UnitTypeId.GATEWAY),
    "observer": (UnitTypeId.OBSERVER, UnitTypeId.ROBOTICSFACILITY),
    "warp_prism": (UnitTypeId.WARPPRISM, UnitTypeId.ROBOTICSFACILITY),
    "immortal": (UnitTypeId.IMMORTAL, UnitTypeId.ROBOTICSFACILITY),
    "colossus": (UnitTypeId.COLOSSUS, UnitTypeId.ROBOTICSFACILITY),
    "disruptor": (UnitTypeId.DISRUPTOR, UnitTypeId.ROBOTICSFACILITY),
    "phoenix": (UnitTypeId.PHOENIX, UnitTypeId.STARGATE),
    "oracle": (UnitTypeId.ORACLE, UnitTypeId.STARGATE),
    "void_ray": (UnitTypeId.VOIDRAY, UnitTypeId.STARGATE),
    "tempest": (UnitTypeId.TEMPEST, UnitTypeId.STARGATE),
    "carrier": (UnitTypeId.CARRIER, UnitTypeId.STARGATE),
    "mothership": (UnitTypeId.MOTHERSHIP, UnitTypeId.NEXUS),
}

_RESEARCH_UPGRADE_IDS: Dict[str, UpgradeId] = {
    "warp_gate": UpgradeId.WARPGATERESEARCH,
    "charge": UpgradeId.CHARGE,
    "blink": UpgradeId.BLINKTECH,
    "shadow_stride": UpgradeId.DARKTEMPLARBLINKUPGRADE,
    "resonating_glaives": UpgradeId.ADEPTPIERCINGATTACK,
    "psionic_storm": UpgradeId.PSISTORMTECH,
    "extended_thermal_lance": UpgradeId.EXTENDEDTHERMALLANCE,
    "gravitic_drive": UpgradeId.GRAVITICDRIVE,
    "gravitic_boosters": UpgradeId.OBSERVERGRAVITICBOOSTER,
    "flux_vanes": UpgradeId.PHOENIXRANGEUPGRADE,
}

for _prefix, _id_prefix in (
    ("ground_weapons", "PROTOSSGROUNDWEAPONSLEVEL"),
    ("ground_armor", "PROTOSSGROUNDARMORSLEVEL"),
    ("shields", "PROTOSSSHIELDSLEVEL"),
    ("air_weapons", "PROTOSSAIRWEAPONSLEVEL"),
    ("air_armor", "PROTOSSAIRARMORSLEVEL"),
):
    for _level in (1, 2, 3):
        _RESEARCH_UPGRADE_IDS[f"{_prefix}_{_level}"] = getattr(UpgradeId, f"{_id_prefix}{_level}")

_TYPE_ALIASES: Dict[str, str] = {
    "ASSIMILATORRICH": "assimilator",
    "WARPGATE": "gateway",
    "WARPPRISMPHASING": "warp_prism",
    "NEXUS": "nexus",
}

for _name, (_type, _producer) in _UNIT_PRODUCER_IDS.items():
    _TYPE_ALIASES[_type.name] = _name
for _name, _type in _BUILDING_UNIT_IDS.items():
    _TYPE_ALIASES.setdefault(_type.name, _name)

_UPGRADE_ALIASES: Dict[str, str] = {}
for _name, _upgrade in _RESEARCH_UPGRADE_IDS.items():
    _UPGRADE_ALIASES[_upgrade.name] = _name


def _catalog_names(action: str, *, kind: Optional[str] = None) -> Tuple[str, ...]:
    specs = targets_for_action(action, race="protoss")
    if kind is None:
        return tuple(spec.name for spec in specs)
    return tuple(spec.name for spec in specs if spec.kind == kind)


def _intersect(ids: Dict[str, Any], names: Tuple[str, ...]) -> Dict[str, Any]:
    return {name: ids[name] for name in names if name in ids}


BUILDINGS: Dict[str, UnitTypeId] = _intersect(
    _BUILDING_UNIT_IDS,
    tuple(name for name in _catalog_names("build", kind="building") if name != "nexus"),
)
UNITS: Dict[str, Tuple[UnitTypeId, UnitTypeId]] = _intersect(
    _UNIT_PRODUCER_IDS,
    _catalog_names("train", kind="unit"),
)
RESEARCH: Dict[str, UpgradeId] = _intersect(
    _RESEARCH_UPGRADE_IDS,
    _catalog_names("research", kind="research"),
)


def combat_unit_types(name: str) -> Tuple[UnitTypeId, ...]:
    """All observable forms of a train target (no separate production credit)."""
    return tuple(type_id for type_id in UnitTypeId if _TYPE_ALIASES.get(type_id.name) == name)


class ProtossTech(Tech):
    def solve_ability(self):
        return self.ai._game_data.upgrades[self.upgrade_type.value].research_ability.exact_id

    def already_pending_upgrade(self, builders) -> float:
        if self.upgrade_type in self.ai.state.upgrades:
            return 1
        ability = self.solve_ability()
        for builder in builders:
            for order in builder.orders:
                if order.ability.exact_id == ability:
                    return max(float(order.progress), 1e-6)
        return 0


class ProtossGridBuilding(GridBuilding):
    """Place on the Protoss grid without creating a Pylon when no spot is free.

    Against Zerg, Sharpy inserts a natural-wall Pylon and its gates at the
    front of the grid. Those points are outside the main. Skip them, and keep
    using main-zone points while any main slot is still open.
    """

    async def start(self, knowledge):
        await super().start(knowledge)
        self.make_pylon = None

    def position_protoss(self, count) -> Optional[Point2]:
        solver = self.building_solver
        grid = getattr(solver, "grid", None)
        if grid is None:
            return super().position_protoss(count)

        is_pylon = self.unit_type == UnitTypeId.PYLON
        buildings = self.ai.structures
        matrix = self.ai.state.psionic_matrix
        points = solver.buildings2x2 if is_pylon else solver.buildings3x3
        wall = set(solver.wall2x2 if is_pylon else solver.wall3x3)
        pending = [] if is_pylon else self.cache.own(UnitTypeId.PYLON).not_ready

        def zone(point: Point2):
            return getattr(grid[point], "ZoneIndex", None)

        def place(candidates) -> Optional[Point2]:
            future = None
            for point in candidates:
                if buildings.closer_than(1, point):
                    continue
                if is_pylon or matrix.covers(point):
                    return point
                if future is None and pending and point.distance_to_closest(pending) <= 7:
                    future = point
            return future

        main = [
            point for point in points
            if point not in wall and zone(point) == ZoneArea.OwnMainZone
        ]
        chosen = place(main)
        if chosen is not None:
            return chosen
        # A free main slot that is not powered yet should wait for its Pylon.
        if any(not buildings.closer_than(1, point) for point in main):
            return None
        others = [point for point in points if point not in wall and point not in main]
        return place(others)


class PlanWarpGateMorph(ActBase):
    """Morph completed idle Gateways after Warp Gate research. No agent action."""

    async def execute(self) -> bool:
        if float(self.ai.already_pending_upgrade(UpgradeId.WARPGATERESEARCH)) < 1:
            return True
        for gate in self.ai.structures(UnitTypeId.GATEWAY).ready.idle:
            gate(AbilityId.MORPH_WARPGATE)
        return True


def _require_catalog(action: str, target: str) -> TargetSpec:
    spec = get_target(target, race="protoss")
    if spec is None:
        raise ValueError(f"unknown catalog target: {target}")
    if spec.action != action:
        raise ValueError(f"catalog target {target!r} is action {spec.action!r}, not {action!r}")
    return spec


class ProtossAdapter(RaceAdapter):
    race_name = "protoss"
    townhall_targets = ("nexus",)

    def production_owned_count(self, snapshot: Any, action: str, target: str) -> int:
        return super().production_owned_count(snapshot, action, target)

    def ability_task_state(self, action: str, snapshot: Any,
                           waiting_for: Optional[str]) -> Tuple[DemandState, Optional[str]]:
        from sc2bench_env.backends.sharpy.races.protoss_execution import ability_task_state
        return ability_task_state(action, snapshot, waiting_for)

    def prerequisite_count_keys(self, prerequisite: str) -> Tuple[str, ...]:
        return (prerequisite,)

    def ability_energy_budget(self, ai: Any, *, available_only: bool = False) -> float:
        nexuses = list(ai.structures(UnitTypeId.NEXUS).ready)
        if available_only:
            used = getattr(ai, "unit_tags_received_action", set())
            nexuses = [nexus for nexus in nexuses if nexus.tag not in used]
        return max((float(getattr(nexus, "energy", 0) or 0) for nexus in nexuses), default=0.0)

    def execution_blocker(self, ai: Any, task: Dict[str, Any]) -> Optional[str]:
        from sc2bench_env.backends.sharpy.races.protoss_execution import execution_blocker
        return execution_blocker(ai, task)

    def resource_committed(self, ai: Any, task: Dict[str, Any]) -> bool:
        from sc2bench_env.backends.sharpy.races.protoss_execution import resource_committed
        return resource_committed(ai, task)

    def ready_progress_target(self, task: Dict[str, Any]) -> Optional[str]:
        from sc2bench_env.backends.sharpy.races.protoss_execution import ready_progress_target
        return ready_progress_target(task)

    def read_ability_facts(self, ai: Any, buildings: Dict[str, int]) -> Dict[str, Any]:
        from sc2bench_env.backends.sharpy.races.protoss_production import read_ability_facts
        return read_ability_facts(ai, buildings, self)

    def count_ready(self, ai: Any, platform_name: str) -> int:
        spec = get_target(platform_name, race=self.race_name)
        if spec is not None and spec.kind == "research":
            return int(any(self.normalize_upgrade_name(upgrade.name) == platform_name
                           for upgrade in getattr(getattr(ai, "state", None), "upgrades", [])))
        if platform_name == "nexus":
            return int(ai.townhalls.ready.amount if hasattr(ai, "townhalls")
                       else ai.structures(UnitTypeId.NEXUS).ready.amount)
        if platform_name == "gateway":
            return int(ai.structures(UnitTypeId.GATEWAY).ready.amount
                       + ai.structures(UnitTypeId.WARPGATE).ready.amount)
        if platform_name in BUILDINGS:
            return int(ai.structures(BUILDINGS[platform_name]).ready.amount)
        if platform_name in UNITS:
            return sum(int(ai.units(unit_type).ready.amount)
                       for unit_type in combat_unit_types(platform_name))
        return 0

    def worker_build_target(self, worker_name: str, order: Any) -> Optional[str]:
        if worker_name != "probe":
            return None
        from sc2bench_env.backends.sharpy.races.protoss_production import worker_build_target
        return worker_build_target(order)

    def production_order_target(self, order: Any, game_data: Any) -> Optional[Tuple[str, str]]:
        from sc2bench_env.backends.sharpy.races.protoss_production import production_order_target
        return production_order_target(order, game_data, self)

    def read_production_capacity(self, ai: Any) -> Optional[list]:
        from sc2bench_env.backends.sharpy.races.protoss_production import read_production_capacity
        return read_production_capacity(ai, self)

    def train_unit_type(self, name: str) -> Optional[UnitTypeId]:
        pair = UNITS.get(name)
        return None if pair is None else pair[0]

    def combat_forms(self, name: str) -> Tuple[UnitTypeId, ...]:
        return combat_unit_types(name)

    def create_act(self, task: Task, *, to_count: int) -> Any:
        if task.action == "build":
            _require_catalog("build", task.target)
            if task.target == "nexus":
                return Expand(to_count)
            if task.target == "assimilator":
                return BuildGas(to_count)
            unit_type = BUILDINGS.get(task.target)
            if unit_type is None:
                raise ValueError(f"unsupported protoss build target: {task.target}")
            if task.target in DEFENSE_KINDS:
                return DefensiveGridBuilding(unit_type, to_count, task.target)
            return ProtossGridBuilding(unit_type, to_count)
        if task.action == "train":
            _require_catalog("train", task.target)
            pair = UNITS.get(task.target)
            if pair is None:
                raise ValueError(f"unsupported protoss train target: {task.target}")
            unit_type, producer = pair
            if task.target in GATEWAY_UNITS:
                return ProtossUnit(unit_type, to_count)
            return ActUnit(unit_type, producer, to_count)
        if task.action == "research":
            _require_catalog("research", task.target)
            upgrade = RESEARCH.get(task.target)
            if upgrade is None:
                raise ValueError(f"unsupported protoss research target: {task.target}")
            spec = get_target(task.target, race="protoss")
            building = _BUILDING_UNIT_IDS.get(spec.produced_at)
            return ProtossTech(upgrade, from_building=building)
        if task.action == "chrono_boost":
            _require_catalog("chrono_boost", "chrono_boost")
            return ActChrono()
        if task.action == "scout":
            _require_catalog("scout", "scout")
            route = task.route or ()
            return ActScoutRoute(route)
        if task.action == "combat":
            style = task.style or ""
            _require_catalog("combat", style)
            return ActCombatMission(style, task.target, task.units or {})
        raise ValueError(f"unsupported protoss action: {task.action}")

    def research_target_from_order(self, ability, game_data) -> Optional[str]:
        if ability is None or game_data is None:
            return None
        ability_id = getattr(ability, "id", ability)
        button = str(getattr(ability, "button_name", ""))
        for name, upgrade in RESEARCH.items():
            data = game_data.upgrades.get(upgrade.value)
            if data is None or data.research_ability is None:
                continue
            exact_id = getattr(ability, "exact_id", None)
            expected_exact = data.research_ability.exact_id
            if exact_id is not None:
                if exact_id != expected_exact:
                    continue
            elif ability_id not in {expected_exact, data.research_ability.id}:
                continue
            if "LEVEL" in upgrade.name and not button.endswith(upgrade.name[-1]):
                continue
            return name
        return None

    def normalize_unit_name(self, type_name: str) -> Optional[str]:
        key = str(type_name or "").upper().replace(" ", "")
        return _TYPE_ALIASES.get(key)

    def normalize_upgrade_name(self, upgrade_name: str) -> Optional[str]:
        key = str(upgrade_name or "").upper().replace(" ", "")
        return _UPGRADE_ALIASES.get(key)

    def is_building_target(self, target: str) -> bool:
        spec = get_target(target, race="protoss")
        return spec is not None and spec.action == "build" and spec.kind == "building"

    def create_tactics(self) -> BuildOrder:
        # Pylons stay agent-owned. AutoPylon is off, matching Terran supply.
        return BuildOrder(
            [
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                PlanZoneDefenseSafe(),
                DistributeWorkers(min_gas=3, aggressive_gas_fill=True),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanHomeGather(self),
                PlanWarpGateMorph(),
            ]
        )


def get_adapter(race: str) -> RaceAdapter:
    race = (race or "").strip().lower()
    if race == "protoss":
        return ProtossAdapter()
    raise ValueError(f"protoss adapter cannot be used for own race {race!r}")
