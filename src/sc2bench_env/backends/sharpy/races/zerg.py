"""Zerg Race Adapter: catalog targets -> Sharpy Acts.

Legal target names and kinds come from Action Catalog. This module only
owns the SC2/Sharpy ID bridge (UnitTypeId / UpgradeId) and act wiring.
Overlords stay agent-owned. Queen Inject is not cast here.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.plans import BuildOrder
from sharpy.plans.acts import (
    ActBase, ActUnit, BuildGas, Expand, GridBuilding, MineOpenBlockedBase, Tech,
)
from sharpy.plans.acts.morph_building import MorphBuilding
from sharpy.plans.build_step import Step
from sharpy.plans.tactics import DistributeWorkers, PlanCancelBuilding, SpeedMining

from sc2bench_env.backends.sharpy.acts import (
    ActCombatMission, ActInject, ActMorphTownhall, ActScoutRoute, ActSpawnCreepTumor,
)
from sc2bench_env.backends.sharpy.defense import PlanZoneDefenseSafe
from sc2bench_env.backends.sharpy.defense_placement import DEFENSE_KINDS, DefensiveGridBuilding
from sc2bench_env.backends.sharpy.gather import PlanHomeGather
from sc2bench_env.backends.sharpy.races.base import RaceAdapter
from sc2bench_env.interface.action_catalog import TargetSpec, get_target, targets_for_action
from sc2bench_env.runtime.task import DemandState, Task

TOWNHALL_TARGETS = frozenset({"hatchery", "lair", "hive"})
LARVA_UNITS = frozenset({
    "drone", "overlord", "zergling", "roach", "hydralisk", "infestor",
    "swarm_host", "mutalisk", "corruptor", "viper", "ultralisk",
})

_BUILDING_UNIT_IDS: Dict[str, UnitTypeId] = {
    "hatchery": UnitTypeId.HATCHERY,
    "extractor": UnitTypeId.EXTRACTOR,
    "spawning_pool": UnitTypeId.SPAWNINGPOOL,
    "evolution_chamber": UnitTypeId.EVOLUTIONCHAMBER,
    "spine_crawler": UnitTypeId.SPINECRAWLER,
    "spore_crawler": UnitTypeId.SPORECRAWLER,
    "roach_warren": UnitTypeId.ROACHWARREN,
    "baneling_nest": UnitTypeId.BANELINGNEST,
    "hydralisk_den": UnitTypeId.HYDRALISKDEN,
    "lurker_den": UnitTypeId.LURKERDENMP,
    "infestation_pit": UnitTypeId.INFESTATIONPIT,
    "spire": UnitTypeId.SPIRE,
    "nydus_network": UnitTypeId.NYDUSNETWORK,
    "ultralisk_cavern": UnitTypeId.ULTRALISKCAVERN,
    "greater_spire": UnitTypeId.GREATERSPIRE,
    "lair": UnitTypeId.LAIR,
    "hive": UnitTypeId.HIVE,
}

_UNIT_PRODUCER_IDS: Dict[str, Tuple[UnitTypeId, UnitTypeId]] = {
    "drone": (UnitTypeId.DRONE, UnitTypeId.LARVA),
    "overlord": (UnitTypeId.OVERLORD, UnitTypeId.LARVA),
    "queen": (UnitTypeId.QUEEN, UnitTypeId.HATCHERY),
    "zergling": (UnitTypeId.ZERGLING, UnitTypeId.LARVA),
    "baneling": (UnitTypeId.BANELING, UnitTypeId.ZERGLING),
    "roach": (UnitTypeId.ROACH, UnitTypeId.LARVA),
    "ravager": (UnitTypeId.RAVAGER, UnitTypeId.ROACH),
    "hydralisk": (UnitTypeId.HYDRALISK, UnitTypeId.LARVA),
    "lurker": (UnitTypeId.LURKERMP, UnitTypeId.HYDRALISK),
    "infestor": (UnitTypeId.INFESTOR, UnitTypeId.LARVA),
    "swarm_host": (UnitTypeId.SWARMHOSTMP, UnitTypeId.LARVA),
    "mutalisk": (UnitTypeId.MUTALISK, UnitTypeId.LARVA),
    "corruptor": (UnitTypeId.CORRUPTOR, UnitTypeId.LARVA),
    "overseer": (UnitTypeId.OVERSEER, UnitTypeId.OVERLORD),
    "viper": (UnitTypeId.VIPER, UnitTypeId.LARVA),
    "ultralisk": (UnitTypeId.ULTRALISK, UnitTypeId.LARVA),
    "brood_lord": (UnitTypeId.BROODLORD, UnitTypeId.CORRUPTOR),
}

_BUILD_MORPHS: Dict[str, Tuple[UnitTypeId, AbilityId, UnitTypeId]] = {
    "lurker_den": (UnitTypeId.HYDRALISKDEN, AbilityId.MORPH_LURKERDEN, UnitTypeId.LURKERDENMP),
    "greater_spire": (
        UnitTypeId.SPIRE, AbilityId.UPGRADETOGREATERSPIRE_GREATERSPIRE, UnitTypeId.GREATERSPIRE,
    ),
}

class BenchMorph(ActBase):
    """Morph an existing unit. Home gathering must not be what decides affordability."""

    def __init__(self, unit_type, ability, result_type, cocoon_type, target_count):
        super().__init__()
        self.unit_type = unit_type
        self.ability_type = ability
        self.result_type = result_type
        self.cocoon_type = cocoon_type
        self.target_count = target_count

    async def execute(self) -> bool:
        done = self.ai.units(self.result_type).amount + self.ai.units(self.cocoon_type).amount
        sources = list(self.ai.units(self.unit_type).ready)
        for unit in sources:
            orders = getattr(unit, "orders", None) or []
            if orders and getattr(getattr(orders[0], "ability", None), "id", None) == self.ability_type:
                done += 1
        if done >= self.target_count:
            return True
        for unit in sources:
            orders = getattr(unit, "orders", None) or []
            if orders and getattr(getattr(orders[0], "ability", None), "id", None) == self.ability_type:
                continue
            if not self.ai.can_afford(self.ability_type, check_supply_cost=False):
                return False
            tags = getattr(self.ai, "bench_group0_tags", None)
            if tags is not None:
                tags.discard(unit.tag)
            unit(self.ability_type)
            done += 1
            if done >= self.target_count:
                return True
        return False


def _morph(unit_type, ability, result_type, cocoon_type):
    class _Specific(BenchMorph):
        def __init__(self, target_count):
            super().__init__(unit_type, ability, result_type, cocoon_type, target_count)

    return _Specific


_UNIT_MORPHS = {
    "baneling": _morph(
        UnitTypeId.ZERGLING, AbilityId.MORPHZERGLINGTOBANELING_BANELING,
        UnitTypeId.BANELING, UnitTypeId.BANELINGCOCOON,
    ),
    "ravager": _morph(
        UnitTypeId.ROACH, AbilityId.MORPHTORAVAGER_RAVAGER,
        UnitTypeId.RAVAGER, UnitTypeId.RAVAGERCOCOON,
    ),
    "lurker": _morph(
        UnitTypeId.HYDRALISK, AbilityId.MORPH_LURKER,
        UnitTypeId.LURKERMP, UnitTypeId.LURKERMPEGG,
    ),
    "overseer": _morph(
        UnitTypeId.OVERLORD, AbilityId.MORPH_OVERSEER,
        UnitTypeId.OVERSEER, UnitTypeId.OVERLORDCOCOON,
    ),
    "brood_lord": _morph(
        UnitTypeId.CORRUPTOR, AbilityId.MORPHTOBROODLORD_BROODLORD,
        UnitTypeId.BROODLORD, UnitTypeId.BROODLORDCOCOON,
    ),
}

_RESEARCH_UPGRADE_IDS: Dict[str, UpgradeId] = {
    "metabolic_boost": UpgradeId.ZERGLINGMOVEMENTSPEED,
    "adrenal_glands": UpgradeId.ZERGLINGATTACKSPEED,
    "glial_reconstitution": UpgradeId.GLIALRECONSTITUTION,
    "tunneling_claws": UpgradeId.TUNNELINGCLAWS,
    "muscular_augments": UpgradeId.EVOLVEMUSCULARAUGMENTS,
    "grooved_spines": UpgradeId.EVOLVEGROOVEDSPINES,
    "lurker_range": UpgradeId.LURKERRANGE,
    "burrow": UpgradeId.BURROW,
    "pneumatized_carapace": UpgradeId.OVERLORDSPEED,
    "chitinous_plating": UpgradeId.CHITINOUSPLATING,
    "anabolic_synthesis": UpgradeId.ANABOLICSYNTHESIS,
    "neural_parasite": UpgradeId.NEURALPARASITE,
    "microbial_shroud": UpgradeId.MICROBIALSHROUD,
}

for _prefix, _id_prefix in (
    ("melee_attacks", "ZERGMELEEWEAPONSLEVEL"),
    ("missile_attacks", "ZERGMISSILEWEAPONSLEVEL"),
    ("ground_carapace", "ZERGGROUNDARMORSLEVEL"),
    ("flyer_attacks", "ZERGFLYERWEAPONSLEVEL"),
    ("flyer_carapace", "ZERGFLYERARMORSLEVEL"),
):
    for _level in (1, 2, 3):
        _RESEARCH_UPGRADE_IDS[f"{_prefix}_{_level}"] = getattr(UpgradeId, f"{_id_prefix}{_level}")

_TYPE_ALIASES: Dict[str, str] = {
    "EXTRACTORRICH": "extractor",
    "LAIR": "lair",
    "HIVE": "hive",
    "GREATERSPIRE": "greater_spire",
    "LURKERDENMP": "lurker_den",
    "OVERSEERSIEGEMODE": "overseer",
    "BANELINGBURROWED": "baneling",
    "DRONEBURROWED": "drone",
    "HYDRALISKBURROWED": "hydralisk",
    "INFESTORBURROWED": "infestor",
    "QUEENBURROWED": "queen",
    "ROACHBURROWED": "roach",
    "ZERGLINGBURROWED": "zergling",
    "ULTRALISKBURROWED": "ultralisk",
    "LURKERMPBURROWED": "lurker",
    "SWARMHOSTBURROWEDMP": "swarm_host",
    "RAVAGERBURROWED": "ravager",
}

for _name, (_type, _producer) in _UNIT_PRODUCER_IDS.items():
    _TYPE_ALIASES[_type.name] = _name
for _name, _type in _BUILDING_UNIT_IDS.items():
    _TYPE_ALIASES.setdefault(_type.name, _name)

_UPGRADE_ALIASES: Dict[str, str] = {}
for _name, _upgrade in _RESEARCH_UPGRADE_IDS.items():
    _UPGRADE_ALIASES[_upgrade.name] = _name


def _catalog_names(action: str, *, kind: Optional[str] = None) -> Tuple[str, ...]:
    specs = targets_for_action(action, race="zerg")
    if kind is None:
        return tuple(spec.name for spec in specs)
    return tuple(spec.name for spec in specs if spec.kind == kind)


def _intersect(ids: Dict[str, Any], names: Tuple[str, ...]) -> Dict[str, Any]:
    return {name: ids[name] for name in names if name in ids}


BUILDINGS: Dict[str, UnitTypeId] = _intersect(
    _BUILDING_UNIT_IDS,
    tuple(name for name in _catalog_names("build", kind="building") if name != "hatchery"),
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


class ZergTech(Tech):
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


class ZergGridBuilding(GridBuilding):
    """Place on creep without creating a Pylon when no spot is free."""

    async def start(self, knowledge):
        await super().start(knowledge)
        self.make_pylon = None


def _require_catalog(action: str, target: str) -> TargetSpec:
    spec = get_target(target, race="zerg")
    if spec is None:
        raise ValueError(f"unknown catalog target: {target}")
    if spec.action != action:
        raise ValueError(f"catalog target {target!r} is action {spec.action!r}, not {action!r}")
    return spec


class ZergAdapter(RaceAdapter):
    race_name = "zerg"
    townhall_targets = ("hatchery", "lair", "hive")

    def production_owned_count(self, snapshot: Any, action: str, target: str) -> int:
        return super().production_owned_count(snapshot, action, target)

    def ability_task_state(self, action: str, snapshot: Any,
                           waiting_for: Optional[str]) -> Tuple[DemandState, Optional[str]]:
        from sc2bench_env.backends.sharpy.races.zerg_execution import ability_task_state
        return ability_task_state(action, snapshot, waiting_for)

    def prerequisite_count_keys(self, prerequisite: str) -> Tuple[str, ...]:
        if prerequisite == "hatchery":
            return ("hatchery", "lair", "hive")
        if prerequisite == "spire":
            return ("spire", "greater_spire")
        if prerequisite == "hydralisk_den":
            return ("hydralisk_den", "lurker_den")
        return (prerequisite,)

    def ability_energy_budget(self, ai: Any, *, available_only: bool = False) -> float:
        queens = list(ai.units(UnitTypeId.QUEEN).ready)
        if available_only:
            used = getattr(ai, "unit_tags_received_action", set())
            queens = [queen for queen in queens if queen.tag not in used]
        return max((float(getattr(queen, "energy", 0) or 0) for queen in queens), default=0.0)

    def execution_blocker(self, ai: Any, task: Dict[str, Any]) -> Optional[str]:
        from sc2bench_env.backends.sharpy.races.zerg_execution import execution_blocker
        return execution_blocker(ai, task)

    def resource_committed(self, ai: Any, task: Dict[str, Any]) -> bool:
        from sc2bench_env.backends.sharpy.races.zerg_execution import resource_committed
        return resource_committed(ai, task)

    def ready_progress_target(self, task: Dict[str, Any]) -> Optional[str]:
        from sc2bench_env.backends.sharpy.races.zerg_execution import ready_progress_target
        return ready_progress_target(task)

    def read_ability_facts(self, ai: Any, buildings: Dict[str, int]) -> Dict[str, Any]:
        from sc2bench_env.backends.sharpy.races.zerg_production import read_ability_facts
        return read_ability_facts(ai, buildings, self)

    def count_ready(self, ai: Any, platform_name: str) -> int:
        spec = get_target(platform_name, race=self.race_name)
        if spec is not None and spec.kind == "research":
            return int(any(self.normalize_upgrade_name(upgrade.name) == platform_name
                           for upgrade in getattr(getattr(ai, "state", None), "upgrades", [])))
        if platform_name in {"hatchery", "lair", "hive"}:
            return int(ai.structures(_BUILDING_UNIT_IDS[platform_name]).ready.amount)
        if platform_name in BUILDINGS:
            return int(ai.structures(BUILDINGS[platform_name]).ready.amount)
        if platform_name in UNITS:
            return sum(int(ai.units(unit_type).ready.amount)
                       for unit_type in combat_unit_types(platform_name))
        return 0

    def worker_build_target(self, worker_name: str, order: Any) -> Optional[str]:
        if worker_name != "drone":
            return None
        from sc2bench_env.backends.sharpy.races.zerg_production import worker_build_target
        return worker_build_target(order)

    def production_order_target(self, order: Any, game_data: Any) -> Optional[Tuple[str, str]]:
        from sc2bench_env.backends.sharpy.races.zerg_production import production_order_target
        return production_order_target(order, game_data, self)

    def read_production_capacity(self, ai: Any) -> Optional[list]:
        from sc2bench_env.backends.sharpy.races.zerg_production import read_production_capacity
        return read_production_capacity(ai, self)

    def train_unit_type(self, name: str) -> Optional[UnitTypeId]:
        pair = UNITS.get(name)
        return None if pair is None else pair[0]

    def combat_forms(self, name: str) -> Tuple[UnitTypeId, ...]:
        return combat_unit_types(name)

    def create_act(self, task: Task, *, to_count: int) -> Any:
        if task.action == "build":
            _require_catalog("build", task.target)
            if task.target == "hatchery":
                return Expand(to_count)
            if task.target == "extractor":
                return BuildGas(to_count)
            morph = _BUILD_MORPHS.get(task.target)
            if morph is not None:
                return MorphBuilding(*morph, to_count)
            unit_type = BUILDINGS.get(task.target)
            if unit_type is None:
                raise ValueError(f"unsupported zerg build target: {task.target}")
            if task.target in DEFENSE_KINDS:
                return DefensiveGridBuilding(unit_type, to_count, task.target)
            return ZergGridBuilding(unit_type, to_count)
        if task.action == "train":
            _require_catalog("train", task.target)
            morph = _UNIT_MORPHS.get(task.target)
            if morph is not None:
                return morph(to_count)
            pair = UNITS.get(task.target)
            if pair is None:
                raise ValueError(f"unsupported zerg train target: {task.target}")
            unit_type, producer = pair
            return ActUnit(unit_type, producer, to_count)
        if task.action == "research":
            _require_catalog("research", task.target)
            upgrade = RESEARCH.get(task.target)
            if upgrade is None:
                raise ValueError(f"unsupported zerg research target: {task.target}")
            spec = get_target(task.target, race="zerg")
            building = _BUILDING_UNIT_IDS.get(spec.produced_at)
            return ZergTech(upgrade, from_building=building)
        if task.action == "upgrade":
            if not task.to:
                raise ValueError("upgrade requires 'to'")
            _require_catalog("upgrade", task.to)
            return ActMorphTownhall(task.target, task.to)
        if task.action == "inject_larva":
            _require_catalog("inject_larva", "inject_larva")
            return ActInject()
        if task.action == "spawn_creep_tumor":
            _require_catalog("spawn_creep_tumor", "spawn_creep_tumor")
            return ActSpawnCreepTumor()
        if task.action == "scout":
            _require_catalog("scout", "scout")
            route = task.route or ()
            return ActScoutRoute(route)
        if task.action == "combat":
            style = task.style or ""
            _require_catalog("combat", style)
            return ActCombatMission(style, task.target, task.units or {})
        raise ValueError(f"unsupported zerg action: {task.action}")

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
        spec = get_target(target, race="zerg")
        return spec is not None and spec.action == "build" and spec.kind == "building"

    def create_tactics(self) -> BuildOrder:
        # Overlords and Inject stay agent-visible facts, not automatic spending.
        return BuildOrder(
            [
                MineOpenBlockedBase(),
                PlanCancelBuilding(),
                PlanZoneDefenseSafe(),
                DistributeWorkers(min_gas=3, aggressive_gas_fill=True),
                Step(None, SpeedMining(), lambda ai: ai.client.game_step > 5),
                PlanHomeGather(self),
            ]
        )


def get_adapter(race: str) -> RaceAdapter:
    race = (race or "").strip().lower()
    if race == "zerg":
        return ZergAdapter()
    raise ValueError(f"zerg adapter cannot be used for own race {race!r}")
