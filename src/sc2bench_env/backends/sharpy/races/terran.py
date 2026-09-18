"""Terran Race Adapter: catalog targets -> Sharpy Acts.

Legal target names and kinds come from Action Catalog. This module only
owns the SC2/Sharpy ID bridge (UnitTypeId / UpgradeId) and act wiring.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.ids.ability_id import AbilityId
from sharpy.plans import BuildOrder
from sharpy.plans.acts import ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech
from sharpy.plans.acts.terran import BuildAddon
from sharpy.plans.tactics import DistributeWorkers, PlanCancelBuilding, SpeedMining
from sharpy.plans.tactics.terran import ContinueBuilding, LowerDepots, Repair
from sharpy.plans.build_step import Step

from sc2bench_env.backends.sharpy.acts import (
    ActCallMule,
    ActCombatMission,
    ActMorphTownhall,
    ActScanZone,
    ActScoutRoute,
)
from sc2bench_env.backends.sharpy.defense import PlanZoneDefenseSafe
from sc2bench_env.backends.sharpy.gather import PlanHomeGather
from sc2bench_env.backends.sharpy.races.base import RaceAdapter
from sc2bench_env.interface.action_catalog import TargetSpec, get_target, targets_for_action
from sc2bench_env.runtime.task import DemandState, Task

TOWNHALL_TARGETS = frozenset({"command_center", "orbital_command", "planetary_fortress"})

# SC2 ID bridge only — membership is intersected with Action Catalog below.
_BUILDING_UNIT_IDS: Dict[str, UnitTypeId] = {
    "ghost_academy": UnitTypeId.GHOSTACADEMY,
    "fusion_core": UnitTypeId.FUSIONCORE,
    "bunker": UnitTypeId.BUNKER,
    "missile_turret": UnitTypeId.MISSILETURRET,
    "sensor_tower": UnitTypeId.SENSORTOWER,
    "supply_depot": UnitTypeId.SUPPLYDEPOT,
    "barracks": UnitTypeId.BARRACKS,
    "factory": UnitTypeId.FACTORY,
    "starport": UnitTypeId.STARPORT,
    "engineering_bay": UnitTypeId.ENGINEERINGBAY,
    "refinery": UnitTypeId.REFINERY,
    "armory": UnitTypeId.ARMORY,
    "command_center": UnitTypeId.COMMANDCENTER,
}

_ADDON_UNIT_IDS: Dict[str, Tuple[UnitTypeId, UnitTypeId]] = {
    "barracks_techlab": (UnitTypeId.BARRACKSTECHLAB, UnitTypeId.BARRACKS),
    "barracks_reactor": (UnitTypeId.BARRACKSREACTOR, UnitTypeId.BARRACKS),
    "factory_techlab": (UnitTypeId.FACTORYTECHLAB, UnitTypeId.FACTORY),
    "factory_reactor": (UnitTypeId.FACTORYREACTOR, UnitTypeId.FACTORY),
    "starport_techlab": (UnitTypeId.STARPORTTECHLAB, UnitTypeId.STARPORT),
    "starport_reactor": (UnitTypeId.STARPORTREACTOR, UnitTypeId.STARPORT),
}

_UNIT_PRODUCER_IDS: Dict[str, Tuple[UnitTypeId, UnitTypeId]] = {
    "reaper": (UnitTypeId.REAPER, UnitTypeId.BARRACKS),
    "ghost": (UnitTypeId.GHOST, UnitTypeId.BARRACKS),
    "hellion": (UnitTypeId.HELLION, UnitTypeId.FACTORY),
    "hellbat": (UnitTypeId.HELLIONTANK, UnitTypeId.FACTORY),
    "widow_mine": (UnitTypeId.WIDOWMINE, UnitTypeId.FACTORY),
    "cyclone": (UnitTypeId.CYCLONE, UnitTypeId.FACTORY),
    "thor": (UnitTypeId.THOR, UnitTypeId.FACTORY),
    "viking": (UnitTypeId.VIKINGFIGHTER, UnitTypeId.STARPORT),
    "liberator": (UnitTypeId.LIBERATOR, UnitTypeId.STARPORT),
    "raven": (UnitTypeId.RAVEN, UnitTypeId.STARPORT),
    "battlecruiser": (UnitTypeId.BATTLECRUISER, UnitTypeId.STARPORT),
    "marine": (UnitTypeId.MARINE, UnitTypeId.BARRACKS),
    "marauder": (UnitTypeId.MARAUDER, UnitTypeId.BARRACKS),
    "siege_tank": (UnitTypeId.SIEGETANK, UnitTypeId.FACTORY),
    "medivac": (UnitTypeId.MEDIVAC, UnitTypeId.STARPORT),
    "banshee": (UnitTypeId.BANSHEE, UnitTypeId.STARPORT),
    "scv": (UnitTypeId.SCV, UnitTypeId.COMMANDCENTER),
}

_RESEARCH_UPGRADE_IDS: Dict[str, UpgradeId] = {
    "personal_cloaking": UpgradeId.PERSONALCLOAKING,
    "infernal_pre_igniter": UpgradeId.HIGHCAPACITYBARRELS,
    "drilling_claws": UpgradeId.DRILLCLAWS,
    "mag_field_accelerator": UpgradeId.CYCLONELOCKONDAMAGEUPGRADE,
    "smart_servos": UpgradeId.SMARTSERVOS,
    "hyperflight_rotors": UpgradeId.BANSHEESPEED,
    "yamato_cannon": UpgradeId.BATTLECRUISERENABLESPECIALIZATIONS,
    "advanced_ballistics": UpgradeId.LIBERATORAGRANGEUPGRADE,
    "caduceus_reactor": UpgradeId.MEDIVACCADUCEUSREACTOR,
    # The old enum labels ID 300 AMPLIFIEDSHIELDING; current Terran game data
    # names that ID InterferenceMatrix. Act checks runtime name before use.
    "interference_matrix": UpgradeId.AMPLIFIEDSHIELDING,
    "hi_sec_auto_tracking": UpgradeId.HISECAUTOTRACKING,
    "neosteel_armor": UpgradeId.TERRANBUILDINGARMOR,
    "stimpack": UpgradeId.STIMPACK,
    "combat_shield": UpgradeId.SHIELDWALL,
    "concussive_shells": UpgradeId.PUNISHERGRENADES,
    "cloaking_field": UpgradeId.BANSHEECLOAK,
    "infantry_weapons_1": UpgradeId.TERRANINFANTRYWEAPONSLEVEL1,
    "infantry_armor_1": UpgradeId.TERRANINFANTRYARMORSLEVEL1,
}

for _prefix, _id_prefix in (
    ("infantry_weapons", "TERRANINFANTRYWEAPONSLEVEL"),
    ("infantry_armor", "TERRANINFANTRYARMORSLEVEL"),
    ("vehicle_weapons", "TERRANVEHICLEWEAPONSLEVEL"),
    ("ship_weapons", "TERRANSHIPWEAPONSLEVEL"),
    ("vehicle_ship_armor", "TERRANVEHICLEANDSHIPARMORSLEVEL"),
):
    for _level in (1, 2, 3):
        _RESEARCH_UPGRADE_IDS[f"{_prefix}_{_level}"] = getattr(UpgradeId, f"{_id_prefix}{_level}")

# Observation aliases (SC2 type name -> catalog name). Not a second target list.
_TYPE_ALIASES: Dict[str, str] = {
    "GHOSTACADEMY": "ghost_academy",
    "FUSIONCORE": "fusion_core",
    "BUNKER": "bunker",
    "MISSILETURRET": "missile_turret",
    "SENSORTOWER": "sensor_tower",
    "VIKINGASSAULT": "viking",
    "LIBERATORAG": "liberator",
    "WIDOWMINEBURROWED": "widow_mine",
    "THORAP": "thor",
    "MULE": "mule",
    "AUTOTURRET": "auto_turret",
    "SUPPLYDEPOT": "supply_depot",
    "SUPPLYDEPOTLOWERED": "supply_depot",
    "BARRACKS": "barracks",
    "BARRACKSFLYING": "barracks",
    "BARRACKSTECHLAB": "barracks_techlab",
    "BARRACKSREACTOR": "barracks_reactor",
    "FACTORYTECHLAB": "factory_techlab",
    "FACTORYREACTOR": "factory_reactor",
    "STARPORTTECHLAB": "starport_techlab",
    "STARPORTREACTOR": "starport_reactor",
    "COMMANDCENTER": "command_center",
    "ORBITALCOMMAND": "orbital_command",
    "PLANETARYFORTRESS": "planetary_fortress",
    "COMMANDCENTERFLYING": "command_center",
    "ORBITALCOMMANDFLYING": "orbital_command",
    "FACTORY": "factory",
    "FACTORYFLYING": "factory",
    "STARPORT": "starport",
    "STARPORTFLYING": "starport",
    "ENGINEERINGBAY": "engineering_bay",
    "ARMORY": "armory",
    "REFINERY": "refinery",
    "REFINERYRICH": "refinery",
    "MARINE": "marine",
    "MARAUDER": "marauder",
    "SIEGETANK": "siege_tank",
    "SIEGETANKSIEGED": "siege_tank",
    "MEDIVAC": "medivac",
    "BANSHEE": "banshee",
    "SCV": "scv",
}

for _name, (_type, _producer) in _UNIT_PRODUCER_IDS.items():
    _TYPE_ALIASES[_type.name] = _name
_UPGRADE_ALIASES: Dict[str, str] = {
    "STIMPACK": "stimpack",
    "SHIELDWALL": "combat_shield",
    "PUNISHERGRENADES": "concussive_shells",
    "BANSHEECLOAK": "cloaking_field",
    "TERRANINFANTRYWEAPONSLEVEL1": "infantry_weapons_1",
    "TERRANINFANTRYARMORSLEVEL1": "infantry_armor_1",
}
for _name, _upgrade in _RESEARCH_UPGRADE_IDS.items():
    _UPGRADE_ALIASES[_upgrade.name] = _name
_UPGRADE_ALIASES["BATTLECRUISERENABLESPECIALIZATIONS"] = "yamato_cannon"
_UPGRADE_ALIASES["YAMATOCANNON"] = "yamato_cannon"


def _catalog_names(action: str, *, kind: Optional[str] = None) -> Tuple[str, ...]:
    specs = targets_for_action(action, race="terran")
    if kind is None:
        return tuple(spec.name for spec in specs)
    return tuple(spec.name for spec in specs if spec.kind == kind)


def _intersect(ids: Dict[str, Any], names: Tuple[str, ...]) -> Dict[str, Any]:
    return {name: ids[name] for name in names if name in ids}


# Public maps used by macro / state: catalog ∩ SC2 bridge.
BUILDINGS: Dict[str, UnitTypeId] = _intersect(
    _BUILDING_UNIT_IDS,
    tuple(name for name in _catalog_names("build", kind="building") if name != "command_center"),
)
ADDONS: Dict[str, Tuple[UnitTypeId, UnitTypeId]] = _intersect(
    _ADDON_UNIT_IDS,
    _catalog_names("build", kind="addon"),
)
UNITS: Dict[str, Tuple[UnitTypeId, UnitTypeId]] = _intersect(
    _UNIT_PRODUCER_IDS,
    _catalog_names("train", kind="unit"),
)
RESEARCH: Dict[str, UpgradeId] = _intersect(
    _RESEARCH_UPGRADE_IDS,
    _catalog_names("research", kind="research"),
)

# The runtime UpgradeData still advertises retired Swarm research abilities
# for combined armour. Current multiplayer Armories expose 864/865/866.
_RESEARCH_ABILITY_OVERRIDES = {
    getattr(UpgradeId, f"TERRANVEHICLEANDSHIPARMORSLEVEL{level}"):
        getattr(AbilityId, f"ARMORYRESEARCH_TERRANVEHICLEANDSHIPPLATINGLEVEL{level}")
    for level in (1, 2, 3)
}


def combat_unit_types(name: str) -> Tuple[UnitTypeId, ...]:
    """All observable forms of a train target (no separate production credit)."""
    return tuple(type_id for type_id in UnitTypeId if _TYPE_ALIASES.get(type_id.name) == name)


class TerranTech(Tech):
    def solve_ability(self):
        # Generic remaps collapse distinct weapon research levels. Dispatch
        # the exact runtime ability and retain known multiplayer overrides.
        if self.upgrade_type in _RESEARCH_ABILITY_OVERRIDES:
            return _RESEARCH_ABILITY_OVERRIDES[self.upgrade_type]
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

    async def execute(self) -> bool:
        if self.upgrade_type == UpgradeId.AMPLIFIEDSHIELDING:
            data = self.ai._game_data.upgrades.get(self.upgrade_type.value)
            if data is None or data.name != "InterferenceMatrix":
                self.failure_reason = "unsupported_research:interference_matrix"
                return True
        return await super().execute()


def _require_catalog(action: str, target: str) -> TargetSpec:
    spec = get_target(target, race="terran")
    if spec is None:
        raise ValueError(f"unknown catalog target: {target}")
    if spec.action != action:
        raise ValueError(f"catalog target {target!r} is action {spec.action!r}, not {action!r}")
    return spec


class TerranAdapter(RaceAdapter):
    race_name = "terran"
    townhall_targets = ("command_center", "orbital_command", "planetary_fortress")

    def production_owned_count(self, snapshot: Any, action: str, target: str) -> int:
        if action == "build" and target == "command_center":
            return sum(snapshot.owned_count(action, name) for name in self.townhall_targets)
        return super().production_owned_count(snapshot, action, target)

    def ability_task_state(self, action: str, snapshot: Any,
                           waiting_for: Optional[str]) -> Tuple[DemandState, Optional[str]]:
        from sc2bench_env.backends.sharpy.races.terran_execution import ability_task_state
        return ability_task_state(action, snapshot, waiting_for)

    def prerequisite_count_keys(self, prerequisite: str) -> Tuple[str, ...]:
        if prerequisite == "command_center":
            return tuple(sorted(TOWNHALL_TARGETS))
        return (prerequisite,)

    def ability_energy_budget(self, ai: Any, *, available_only: bool = False) -> float:
        if available_only:
            from sc2bench_env.backends.sharpy.acts import available_orbitals
            return max((float(o.energy) for o in available_orbitals(ai)), default=0.0)
        orbitals = ai.structures(UnitTypeId.ORBITALCOMMAND).ready
        return max((float(getattr(o, "energy", 0) or 0) for o in orbitals), default=0.0)

    def execution_blocker(self, ai: Any, task: Dict[str, Any]) -> Optional[str]:
        from sc2bench_env.backends.sharpy.races.terran_execution import execution_blocker
        return execution_blocker(ai, task)

    def resource_committed(self, ai: Any, task: Dict[str, Any]) -> bool:
        from sc2bench_env.backends.sharpy.races.terran_execution import resource_committed
        return resource_committed(ai, task)

    def ready_progress_target(self, task: Dict[str, Any]) -> Optional[str]:
        from sc2bench_env.backends.sharpy.races.terran_execution import ready_progress_target
        return ready_progress_target(task)

    def read_ability_facts(self, ai: Any, buildings: Dict[str, int]) -> Dict[str, Any]:
        from sc2bench_env.backends.sharpy.races.terran_production import read_ability_facts
        return read_ability_facts(ai, buildings, self)

    def count_ready(self, ai: Any, platform_name: str) -> int:
        spec = get_target(platform_name, race=self.race_name)
        if spec is not None and spec.kind == "research":
            return int(any(self.normalize_upgrade_name(upgrade.name) == platform_name
                           for upgrade in getattr(getattr(ai, "state", None), "upgrades", [])))
        if platform_name in {"orbital_command", "planetary_fortress"}:
            unit_type = (UnitTypeId.ORBITALCOMMAND if platform_name == "orbital_command"
                         else UnitTypeId.PLANETARYFORTRESS)
            return len(ai.structures(unit_type).ready)
        if platform_name == "command_center":
            return int(ai.townhalls.ready.amount if hasattr(ai, "townhalls")
                       else ai.structures.of_type([
                           UnitTypeId.COMMANDCENTER, UnitTypeId.ORBITALCOMMAND,
                           UnitTypeId.PLANETARYFORTRESS]).ready.amount)
        if platform_name == "supply_depot":
            return int(ai.structures(UnitTypeId.SUPPLYDEPOT).ready.amount
                       + ai.structures(UnitTypeId.SUPPLYDEPOTLOWERED).ready.amount)
        if platform_name in BUILDINGS:
            return int(ai.structures(BUILDINGS[platform_name]).ready.amount)
        if platform_name in ADDONS:
            addon_type, parent_type = ADDONS[platform_name]
            attached_tags = {int(parent.add_on_tag) for parent in ai.structures(parent_type).ready
                             if getattr(parent, "add_on_tag", 0)}
            return sum(1 for addon in ai.structures(addon_type).ready if addon.tag in attached_tags)
        if platform_name in UNITS:
            unit_type, _ = UNITS[platform_name]
            return int(ai.units(unit_type).ready.amount)
        return 0

    def worker_build_target(self, worker_name: str, order: Any) -> Optional[str]:
        if worker_name != "scv":
            return None
        from sc2bench_env.backends.sharpy.races.terran_production import worker_build_target
        return worker_build_target(order)

    def production_order_target(self, order: Any, game_data: Any) -> Optional[Tuple[str, str]]:
        from sc2bench_env.backends.sharpy.races.terran_production import production_order_target
        return production_order_target(order, game_data, self)

    def read_production_capacity(self, ai: Any) -> Optional[list]:
        from sc2bench_env.backends.sharpy.races.terran_production import read_production_capacity
        return read_production_capacity(ai, self)

    def create_act(self, task: Task, *, to_count: int) -> Any:
        if task.action == "build":
            _require_catalog("build", task.target)
            if task.target == "command_center":
                return Expand(to_count)
            if task.target == "refinery":
                return BuildGas(to_count)
            if task.target in ADDONS:
                addon_type, parent_type = ADDONS[task.target]
                return BuildAddon(addon_type, parent_type, to_count)
            unit_type = BUILDINGS.get(task.target)
            if unit_type is None:
                raise ValueError(f"unsupported terran build target: {task.target}")
            return GridBuilding(unit_type, to_count)
        if task.action == "train":
            _require_catalog("train", task.target)
            pair = UNITS.get(task.target)
            if pair is None:
                raise ValueError(f"unsupported terran train target: {task.target}")
            unit_type, producer = pair
            return ActUnit(unit_type, producer, to_count)
        if task.action == "expand":
            return Expand(to_count)
        if task.action == "research":
            _require_catalog("research", task.target)
            upgrade = RESEARCH.get(task.target)
            if upgrade is None:
                raise ValueError(f"unsupported terran research target: {task.target}")
            # Modern patches moved some research buildings; the catalog is the
            # supported platform contract, not burnysc2's historical dictionary.
            spec = get_target(task.target, race="terran")
            building = _BUILDING_UNIT_IDS.get(spec.produced_at)
            if spec.produced_at in ADDONS:
                building = ADDONS[spec.produced_at][0]
            return TerranTech(upgrade, from_building=building)

        if task.action == "upgrade":
            if not task.to:
                raise ValueError("upgrade requires 'to'")
            _require_catalog("upgrade", task.to)
            return ActMorphTownhall(task.target, task.to)
        if task.action == "scan":
            _require_catalog("scan", "scan")
            return ActScanZone(task.target)
        if task.action == "call_mule":
            _require_catalog("call_mule", "call_mule")
            return ActCallMule()
        if task.action == "scout":
            _require_catalog("scout", "scout")
            route = task.route or ()
            return ActScoutRoute(route)
        if task.action == "combat":
            style = task.style or ""
            _require_catalog("combat", style)
            return ActCombatMission(style, task.target, task.units or {})
        raise ValueError(f"unsupported terran action: {task.action}")

    def research_target_from_order(self, ability, game_data) -> Optional[str]:
        if ability is None or game_data is None:
            return None
        ability_id = getattr(ability, "id", ability)
        button = str(getattr(ability, "button_name", ""))
        for name, upgrade in RESEARCH.items():
            if upgrade == UpgradeId.YAMATOCANNON:
                upgrade = UpgradeId.BATTLECRUISERENABLESPECIALIZATIONS
            data = game_data.upgrades.get(upgrade.value)
            if data is None or data.research_ability is None:
                continue
            exact_id = getattr(ability, "exact_id", None)
            expected_exact = _RESEARCH_ABILITY_OVERRIDES.get(upgrade, data.research_ability.exact_id)
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
        spec = get_target(target, race="terran")
        return spec is not None and spec.action == "build" and spec.kind in {"building", "addon"}

    def create_tactics(self) -> BuildOrder:
        # Phase 1: AutoDepot() stays OFF. Supply is entirely agent-owned via
        # {"action":"build","target":"supply_depot"}. Optional non-benchmark
        # AutoDepot fallback is deferred (PLATFORM_PLAN §11).
        return BuildOrder(
            [
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                LowerDepots(),
                PlanZoneDefenseSafe(),
                DistributeWorkers(min_gas=3, aggressive_gas_fill=True),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                Repair(),
                ContinueBuilding(),
                PlanHomeGather(self),
            ]
        )


def get_adapter(race: str) -> RaceAdapter:
    race = (race or "terran").strip().lower()
    if race == "terran":
        return TerranAdapter()
    raise ValueError(f"Phase 2 still uses terran reference race only; got {race!r}")
