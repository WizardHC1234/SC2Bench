"""Instant / ability-style Sharpy acts used by the platform adapter."""
from __future__ import annotations


from math import isfinite
from typing import Dict, List, Mapping, Optional, Sequence

from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

from sc2bench_env.backends.sharpy.combat_styles import unit_available_for_background
from sc2bench_env.catalog.registry import get_target
from sc2bench_env.interface.actions import ScoutRoute
from sc2bench_env.runtime.scouting import order_expansions

_MULE_ENERGY = float(get_target("call_mule", race="terran").energy)
_SCAN_ENERGY = float(get_target("scan", race="terran").energy)
_SUPPLY_DROP_ENERGY = float(get_target("supply_drop", race="terran").energy)
_CHRONO_ENERGY = float(get_target("chrono_boost", race="protoss").energy)
_INJECT_ENERGY = float(get_target("inject_larva", race="zerg").energy)
_TUMOR_ENERGY = float(get_target("spawn_creep_tumor", race="zerg").energy)
_TOWNHALL_BUILD = frozenset({
    AbilityId.TERRANBUILD_COMMANDCENTER,
    AbilityId.PROTOSSBUILD_NEXUS,
    AbilityId.ZERGBUILD_HATCHERY,
})


def available_orbitals(ai, energy: float = 0):
    """Do not reuse a caster whose energy/action is still from this frame.

    BurnySC2 records queued unit tags immediately, but observes energy changes
    on the next frame. One cast per Orbital per frame avoids conflicting orders;
    another Orbital remains independently available (energy cannot be pooled).
    """
    used = getattr(ai, "unit_tags_received_action", set())
    return [orbital for orbital in ai.structures(UnitTypeId.ORBITALCOMMAND).ready
            if orbital.tag not in used and orbital.energy >= energy]


_UPGRADE_SPECS = {
    "orbital_command": (
        UnitTypeId.COMMANDCENTER,
        AbilityId.UPGRADETOORBITAL_ORBITALCOMMAND,
        UnitTypeId.ORBITALCOMMAND,
    ),
    "planetary_fortress": (
        UnitTypeId.COMMANDCENTER,
        AbilityId.UPGRADETOPLANETARYFORTRESS_PLANETARYFORTRESS,
        UnitTypeId.PLANETARYFORTRESS,
    ),
    "lair": (
        UnitTypeId.HATCHERY,
        AbilityId.UPGRADETOLAIR_LAIR,
        UnitTypeId.LAIR,
    ),
    "hive": (
        UnitTypeId.LAIR,
        AbilityId.UPGRADETOHIVE_HIVE,
        UnitTypeId.HIVE,
    ),
}


def _scan_hotspot_position(hotspot) -> Optional[Point2]:
    """Sharpy returns HeatArea.center despite annotating its API as Point2.

    Older forks can expose .position instead. Never pass an opaque heat-area
    object (or a non-finite point) to BurnySC2's unit-command target validator.
    """
    for candidate in (hotspot, getattr(hotspot, "center", None),
                      getattr(hotspot, "position", None)):
        if isinstance(candidate, Point2) and isfinite(candidate.x) and isfinite(candidate.y):
            return candidate
    return None


class ActScanZone(ActBase):
    """Cast one Scanner Sweep on a stable zone_id when an Orbital has energy."""

    def __init__(self, zone_id: str):
        super().__init__()
        self.zone_id = zone_id
        self._done = False
        self.failure_reason: Optional[str] = None

    async def execute(self) -> bool:
        if self._done or self.failure_reason:
            return True

        registry = getattr(self.ai, "zone_registry", None)
        zone = None
        target: Optional[Point2] = None
        if registry is not None:
            zone = registry.resolve_zone(self.zone_manager, self.zone_id)
            if zone is not None:
                # Prefer enemy heat inside the zone when HeatMap is available.
                manager = None
                try:
                    from sharpy.managers.extensions import HeatMapManager

                    manager = self.knowledge.get_manager(HeatMapManager)
                except Exception:
                    manager = None
                if manager is not None and hasattr(manager, "get_zones_hotspot"):
                    hotspot = manager.get_zones_hotspot([zone])
                    target = _scan_hotspot_position(hotspot)
                if target is None:
                    target = zone.center_location
            else:
                center = registry.center_for(self.zone_id)
                if center is not None:
                    target = Point2(center)

        if target is None:
            self.failure_reason = f"invalid_zone:{self.zone_id}"
            return True

        for orbital in available_orbitals(self.ai, _SCAN_ENERGY):
            if orbital(AbilityId.SCANNERSWEEP_SCAN, target):
                self._done = True
                return True
        return False


class ActMorphTownhall(ActBase):
    """Morph a specific Command Center identified by stable structure id."""

    def __init__(self, structure_id: str, to: str):
        super().__init__()
        self.structure_id = structure_id
        self.to = to
        self._done = False
        self.failure_reason: Optional[str] = None

    async def execute(self) -> bool:
        if self._done or self.failure_reason:
            return True

        spec = _UPGRADE_SPECS.get(self.to)
        if spec is None:
            self.failure_reason = f"unsupported_upgrade_to:{self.to}"
            return True
        from_type, ability, result_type = spec

        registry = getattr(self.ai, "structure_registry", None)
        if registry is None:
            self.failure_reason = "structure_registry_missing"
            return True
        tag = registry.tag_for_id(self.structure_id)
        if tag is None:
            self.failure_reason = f"unknown_structure:{self.structure_id}"
            return True

        unit = self.ai.structures.find_by_tag(tag)
        if unit is None:
            self.failure_reason = f"structure_gone:{self.structure_id}"
            return True

        # Already morphing / already the target type => action success.
        if unit.type_id == result_type:
            self._done = True
            return True
        if unit.type_id != from_type:
            label = "not_command_center" if from_type == UnitTypeId.COMMANDCENTER else "wrong_structure"
            self.failure_reason = f"{label}:{self.structure_id}"
            return True
        if not unit.is_ready:
            return False

        # Planetary needs Engineering Bay; Orbital needs Barracks.
        if self.to == "orbital_command" and self.ai.structures(UnitTypeId.BARRACKS).ready.amount <= 0:
            return False
        if (
            self.to == "planetary_fortress"
            and self.ai.structures(UnitTypeId.ENGINEERINGBAY).ready.amount <= 0
        ):
            return False
        if self.to == "lair" and self.ai.structures(UnitTypeId.SPAWNINGPOOL).ready.amount <= 0:
            return False
        if self.to == "hive" and self.ai.structures(UnitTypeId.INFESTATIONPIT).ready.amount <= 0:
            return False

        unit(ability)
        # Issuing the morph ends the upgrade action; completion is world state.
        self._done = True
        return True


class ActCallMule(ActBase):
    """Call down one MULE on a safe mineral-rich own base."""

    def __init__(self):
        super().__init__()
        self._done = False
        self.failure_reason: Optional[str] = None

    async def execute(self) -> bool:
        if self._done or self.failure_reason:
            return True

        orbitals = available_orbitals(self.ai, _MULE_ENERGY)
        if not orbitals:
            return False

        target = self._solve_target()
        if target is None:
            self.failure_reason = "no_safe_mineral_target"
            return True

        for orbital in orbitals:
            if orbital(AbilityId.CALLDOWNMULE_CALLDOWNMULE, target):
                self._done = True
                return True
        return False

    def _solve_target(self):
        best = None
        best_score = -1.0
        zones = list(getattr(self.zone_manager, "expansion_zones", None) or [])
        for zone in zones:
            if not getattr(zone, "is_ours", False):
                continue
            if getattr(zone, "is_under_attack", False):
                continue
            fields = getattr(zone, "mineral_fields", None)
            if fields is None or not fields.exists:
                continue
            townhall = getattr(zone, "our_townhall", None)
            if townhall is None or not townhall.is_ready:
                continue
            remaining = 0.0
            for mineral in fields:
                remaining += float(getattr(mineral, "mineral_contents", 0) or 0)
            if remaining > best_score:
                best_score = remaining
                best = fields.random
        return best


class ActSupplyDrop(ActBase):
    """Spend one Orbital's energy on one completed Supply Depot the platform chooses."""

    def __init__(self):
        super().__init__()
        self._done = False
        self.failure_reason: Optional[str] = None
        self.supply_granted = 0

    async def execute(self) -> bool:
        if self._done or self.failure_reason:
            return True
        orbitals = available_orbitals(self.ai, _SUPPLY_DROP_ENERGY)
        if not orbitals:
            return False
        depots = _eligible_supply_depots(self.ai)
        if not depots:
            self.failure_reason = "no_legal_depot"
            return True
        orbital = max(orbitals, key=lambda unit: float(getattr(unit, "energy", 0) or 0))
        depot = min(depots, key=lambda unit: unit.distance_to(orbital))
        if orbital(AbilityId.SUPPLYDROP_SUPPLYDROP, depot):
            from sc2bench_env.catalog.knowledge import food_provided

            self.supply_granted = int(food_provided("terran", "supply_depot"))
            self._done = True
            return True
        return False


def _eligible_supply_depots(ai):
    """Completed living depots that have not already received a drop. Lowered depots count."""
    found = []
    for type_id in (UnitTypeId.SUPPLYDEPOT, UnitTypeId.SUPPLYDEPOTLOWERED):
        structures = ai.structures(type_id)
        ready = getattr(structures, "ready", structures)
        for depot in ready:
            if float(getattr(depot, "health", 1) or 0) <= 0:
                continue
            if not getattr(depot, "is_ready", True):
                continue
            if BuffId.SUPPLYDROP in (getattr(depot, "buffs", ()) or ()):
                continue
            found.append(depot)
    return found


def _has_buff(unit, buff) -> bool:
    return buff in (getattr(unit, "buffs", ()) or ())


class ActChrono(ActBase):
    """Spend one Nexus Chrono Boost on a structure the backend chooses."""

    def __init__(self):
        super().__init__()
        self._done = False
        self.failure_reason: Optional[str] = None

    async def execute(self) -> bool:
        if self._done or self.failure_reason:
            return True
        used = getattr(self.ai, "unit_tags_received_action", set())
        nexuses = [
            nexus for nexus in self.ai.structures(UnitTypeId.NEXUS).ready
            if nexus.tag not in used and float(getattr(nexus, "energy", 0) or 0) >= _CHRONO_ENERGY
        ]
        if not nexuses:
            return False
        target = self._target()
        if target is None:
            self.failure_reason = "no_chrono_target"
            return True
        caster = min(nexuses, key=lambda nexus: nexus.distance_to(target))
        if caster(AbilityId.EFFECT_CHRONOBOOSTENERGYCOST, target):
            self._done = True
            return True
        return False

    def _target(self):
        skipped = {UnitTypeId.PYLON, UnitTypeId.ASSIMILATOR}
        open_targets = []
        for structure in self.ai.structures.ready:
            if structure.type_id in skipped or _has_buff(structure, BuffId.CHRONOBOOSTENERGYCOST):
                continue
            open_targets.append(structure)
        if not open_targets:
            return None
        working = [structure for structure in open_targets if getattr(structure, "orders", None)]
        pool = working or open_targets
        producing = [structure for structure in pool if structure.type_id != UnitTypeId.NEXUS]
        return (producing or pool)[0]


class ActInject(ActBase):
    """Spend one Queen inject on a town hall that does not already have larva incoming."""

    def __init__(self):
        super().__init__()
        self._done = False
        self.failure_reason: Optional[str] = None
        self.waiting_reason = None

    async def execute(self) -> bool:
        if self._done or self.failure_reason:
            return True
        self.waiting_reason = None
        queens = [
            queen for queen in self.ai.units(UnitTypeId.QUEEN).ready
            if float(getattr(queen, "energy", 0) or 0) >= _INJECT_ENERGY
            and unit_available_for_background(queen, self.ai, require_group0=False)
        ]
        if not queens:
            self.waiting_reason = (
                "queen_unavailable" if list(self.ai.units(UnitTypeId.QUEEN).ready) else "prerequisite:queen"
            )
            return False
        halls = [
            hall for hall in self.ai.townhalls.ready
            if not _has_buff(hall, BuffId.QUEENSPAWNLARVATIMER)
        ]
        if not halls:
            self.failure_reason = "no_inject_target"
            return True
        queen, hall = min(
            ((queen, hall) for queen in queens for hall in halls),
            key=lambda pair: pair[0].distance_to(pair[1]),
        )
        if queen(AbilityId.EFFECT_INJECTLARVA, hall):
            self._done = True
            return True
        return False


class ActSpawnCreepTumor(ActBase):
    """Plant one creep tumor. A burrowed tumor spreads first; otherwise a Queen does."""

    def __init__(self):
        super().__init__()
        self._done = False
        self.failure_reason: Optional[str] = None
        self.waiting_reason = None

    async def execute(self) -> bool:
        if self._done or self.failure_reason:
            return True
        self.waiting_reason = None
        used = getattr(self.ai, "unit_tags_received_action", set())
        tumors = [
            tumor for tumor in self.ai.structures(UnitTypeId.CREEPTUMORBURROWED).ready
            if tumor.tag not in used
        ]
        for tumor in tumors:
            point = self._ahead(tumor.position, 8)
            if point is not None and tumor(AbilityId.BUILD_CREEPTUMOR_TUMOR, point):
                self._done = True
                return True
        queens = [
            queen for queen in self.ai.units(UnitTypeId.QUEEN).ready
            if float(getattr(queen, "energy", 0) or 0) >= _TUMOR_ENERGY
            and unit_available_for_background(queen, self.ai, require_group0=False)
        ]
        if not queens:
            self.waiting_reason = (
                "queen_unavailable" if list(self.ai.units(UnitTypeId.QUEEN).ready) else "prerequisite:queen"
            )
            return False
        queen = queens[0]
        point = self._ahead(queen.position, 5)
        if point is None:
            self.failure_reason = "no_creep_for_tumor"
            return True
        if queen(AbilityId.BUILD_CREEPTUMOR_QUEEN, point):
            self._done = True
            return True
        return False

    def _ahead(self, origin, distance: float):
        """A creep point toward the enemy, or back on the town hall's creep."""
        starts = list(getattr(self.ai, "enemy_start_locations", None) or [])
        goal = starts[0] if starts else origin
        candidates = [origin.towards(goal, step) for step in (distance, 3, 2)]
        for hall in getattr(self.ai, "townhalls", ()) or ():
            center = hall.position
            candidates.append(center.towards(goal, 4))
            candidates.append(center.towards(goal, 2))
        has_creep = getattr(self.ai, "has_creep", None)
        seen = set()
        for point in candidates:
            key = (round(float(point.x), 1), round(float(point.y), 1))
            if key in seen:
                continue
            seen.add(key)
            if callable(has_creep) and not has_creep(point):
                continue
            return point
        return None


class ActScoutRoute(ActBase):
    """Send one SCV along an ordered list of stable zone_ids."""

    def __init__(self, route: ScoutRoute):
        super().__init__()
        self._automatic = route == "all"
        self._route_planned = not self._automatic
        self.route: List[str] = [] if self._automatic else [str(item) for item in route]
        self._index = 0
        self._scout_tag: Optional[int] = None
        self._done = False
        self.failure_reason: Optional[str] = None
        self.current_zone: Optional[str] = None

    async def execute(self) -> bool:
        if self._done or self.failure_reason:
            return True
        registry = getattr(self.ai, "zone_registry", None)
        if registry is None:
            self.failure_reason = "zone_registry_missing"
            return True

        if not self._route_planned:
            candidates = []
            for zone_id in registry.zone_ids:
                zone = registry.resolve_zone(self.zone_manager, zone_id)
                if zone is not None and getattr(zone, "is_ours", False):
                    continue
                center = zone.center_location if zone is not None else registry.center_for(zone_id)
                if center is None:
                    self.failure_reason = f"invalid_zone:{zone_id}"
                    return True
                visible = getattr(self.ai, "is_visible", lambda point: False)(Point2(center))
                candidates.append((zone_id, center, visible))
            self.route = order_expansions(candidates, self.ai.start_location)
            self._route_planned = True
            if not self.route:
                self._done = True
                return True
        if not self.route:
            self.failure_reason = "empty_route"
            return True

        scout = None
        if self._scout_tag is not None:
            scout = self.ai.workers.find_by_tag(self._scout_tag)
            if scout is None:
                self.failure_reason = "scout_died"
                return True

        while self._index < len(self.route):
            zone_id = self.route[self._index]
            self.current_zone = zone_id
            zone = registry.resolve_zone(self.zone_manager, zone_id)
            target = None
            if zone is not None:
                target = zone.center_location
            else:
                center = registry.center_for(zone_id)
                if center is not None:
                    target = Point2(center)
            if target is None:
                self.failure_reason = f"invalid_zone:{zone_id}"
                return True

            if scout is None:
                workers = self.ai.workers.gathering | self.ai.workers.idle
                if not workers.exists:
                    return False
                scout = min(workers, key=lambda worker: worker.distance_to(target))
                self._scout_tag = scout.tag

            if scout.distance_to(target) <= 6:
                self._index += 1
                continue
            from sc2bench_env.backends.sharpy.zerg_creep import is_creep_overlord, maintain_station_creep
            if is_creep_overlord(scout) and maintain_station_creep(self.ai, scout, False) == "toggled":
                return False
            scout.move(target)
            return False

        self._done = True
        self.current_zone = None
        return True


_GROUND_CARGO = frozenset({"marine", "marauder"})
_LOAD_RADIUS = 5.0
_UNLOAD_RADIUS = 4.0
_WITHDRAW_PICKUP_RADIUS = 12.0
_CARRIER_LOAD = {
    UnitTypeId.MEDIVAC: AbilityId.LOAD_MEDIVAC,
    UnitTypeId.WARPPRISM: AbilityId.LOAD_WARPPRISM,
    UnitTypeId.OVERLORDTRANSPORT: AbilityId.LOAD_OVERLORD,
}
_CARRIER_UNLOAD = {
    UnitTypeId.MEDIVAC: AbilityId.UNLOADALLAT_MEDIVAC,
    UnitTypeId.WARPPRISM: AbilityId.UNLOADALLAT_WARPPRISM,
    UnitTypeId.OVERLORDTRANSPORT: AbilityId.UNLOADALLAT_OVERLORD,
}
_ARRIVAL_RADIUS = 12.0

# Alternate forms keyed for combat evidence / Observation.forms.
_FORM_TYPE_KEYS = {
    UnitTypeId.WIDOWMINEBURROWED: "widow_mine_burrowed",
    UnitTypeId.LIBERATORAG: "liberator_ag",
    UnitTypeId.VIKINGASSAULT: "viking_assault",
    UnitTypeId.THORAP: "thor_ap",
    UnitTypeId.SIEGETANKSIEGED: "siege_tank_sieged",
    UnitTypeId.OBSERVERSIEGEMODE: "observer_sieged",
    UnitTypeId.WARPPRISMPHASING: "warp_prism_phasing",
    UnitTypeId.OVERSEERSIEGEMODE: "overseer_sieged",
    UnitTypeId.BANELINGBURROWED: "baneling_burrowed",
    UnitTypeId.ROACHBURROWED: "roach_burrowed",
    UnitTypeId.QUEENBURROWED: "queen_burrowed",
    UnitTypeId.INFESTORBURROWED: "infestor_burrowed",
    UnitTypeId.LURKERMPBURROWED: "lurker_burrowed",
    UnitTypeId.RAVAGERBURROWED: "ravager_burrowed",
    UnitTypeId.SWARMHOSTBURROWEDMP: "swarm_host_burrowed",
    UnitTypeId.ZERGLINGBURROWED: "zergling_burrowed",
    UnitTypeId.HYDRALISKBURROWED: "hydralisk_burrowed",
    UnitTypeId.ULTRALISKBURROWED: "ultralisk_burrowed",
}

_SKILL_ORDER_HINTS = (
    ("EMP", "ghost_emp"),
    ("GHOSTSNIPE", "ghost_snipe"),
    ("SNIPE", "ghost_snipe"),
    ("LOCKON", "cyclone_lock"),
    ("LOCK_ON", "cyclone_lock"),
    ("INTERFERENCEMATRIX", "raven_matrix"),
    ("RAVENSCRAMBLER", "raven_matrix"),
    ("ANTIARMOR", "raven_antiarmor"),
    ("SHREDDERMISSILE", "raven_antiarmor"),
    ("AUTOTURRET", "raven_turret_order"),
    ("BUILDAUTOTURRET", "raven_turret_order"),
    ("YAMATO", "bc_yamato"),
    ("TACTICALJUMP", "bc_jump"),
    ("EFFECT_BLINK", "stalker_blink"),
    ("GRAVITONBEAM_GRAVITONBEAM", "phoenix_beam"),
    ("FORCEFIELD_FORCEFIELD", "sentry_force_field"),
    ("GUARDIANSHIELD_GUARDIANSHIELD", "sentry_guardian_shield"),
    ("HALLUCINATION_", "sentry_hallucination"),
    ("PSISTORM_PSISTORM", "high_templar_storm"),
    ("FEEDBACK_FEEDBACK", "high_templar_feedback"),
    ("PURIFICATIONNOVA_PURIFICATIONNOVA", "disruptor_nova"),
    ("ORACLEREVELATION_", "oracle_revelation"),
    ("ORACLESTASISTRAP_", "oracle_stasis"),
    ("EFFECT_TIMEWARP", "mothership_time_warp"),
    ("EFFECT_MASSRECALL_MOTHERSHIP", "mothership_recall"),
    ("EFFECT_CORROSIVEBILE", "ravager_bile"),
    ("TRANSFUSION_TRANSFUSION", "queen_transfuse"),
    ("FUNGALGROWTH_FUNGALGROWTH", "infestor_fungal"),
    ("NEURALPARASITE_NEURALPARASITE", "infestor_neural"),
    ("EFFECT_SPAWNLOCUSTS", "swarm_host_locust"),
    ("SWARMHOSTSPAWNLOCUSTS", "swarm_host_locust"),
    ("PARASITICBOMB_PARASITICBOMB", "viper_parasitic_bomb"),
    ("BLINDINGCLOUD_BLINDINGCLOUD", "viper_blinding_cloud"),
    ("EFFECT_ABDUCT", "viper_abduct"),
    ("LOAD_OVERLORD", "overlord_load"),
    ("UNLOADALLAT_OVERLORD", "overlord_unload"),
    ("LOAD_WARPPRISM", "warp_prism_load"),
    ("UNLOADALLAT_WARPPRISM", "warp_prism_unload"),
    ("MORPH_WARPPRISMPHASINGMODE", "warp_prism_phasing"),
    ("MORPH_SURVEILLANCEMODE", "observer_sieged"),
    ("MORPH_OVERSIGHTMODE", "overseer_sieged"),
    ("BURROWDOWN_ROACH", "roach_burrowed"),
    ("BURROWDOWN_BANELING", "baneling_burrowed"),
    ("BURROWDOWN_INFESTOR", "infestor_burrowed"),
    ("BURROWDOWN_LURKER", "lurker_burrowed"),
    ("BURROWDOWN_RAVAGER", "ravager_burrowed"),
    ("BURROWDOWN_QUEEN", "queen_burrowed"),
    ("BURROWDOWN_SWARMHOST", "swarm_host_burrowed"),
    ("BURROWDOWN_ZERGLING", "zergling_burrowed"),
    ("BURROWDOWN_HYDRALISK", "hydralisk_burrowed"),
    ("BURROWDOWN_ULTRALISK", "ultralisk_burrowed"),
    ("VIPERCONSUME", "viper_consume"),
    ("SUPPLYDROP_SUPPLYDROP", "supply_drop"),
    ("EFFECT_MASSRECALL_NEXUS", "nexus_recall"),
    ("BEHAVIOR_GENERATECREEPON", "generate_creep_on"),
    ("BEHAVIOR_GENERATECREEPOFF", "generate_creep_off"),
)


class ActCombatMission(ActBase):
    """Bind home army and execute attack/defend with backend transport micro.

    Stim / Tank / heal come from MicroRules. Banshee cloak is platform micro.
    Transport is a local execution decision, not an Agent-selected style.
    Passengers stay mission-owned and count toward alive composition.
    A losing fight does not end the mission; the Agent orders retreat.
    """

    def __init__(self, style: str, zone_id: str, units: Mapping[str, int]):
        super().__init__()
        self.style = str(style)
        self.zone_id = str(zone_id)
        self.units = {str(name): int(count) for name, count in dict(units or {}).items()}
        self._tags: List[int] = []
        self._bound = False
        self.end_reason: Optional[str] = None
        self.failure_reason: Optional[str] = None
        self.alive_counts: Dict[str, int] = dict(self.units)
        self.phase: str = "fight"
        self._micro_rules = None
        self._micro_started = False
        self._hold_point: Optional[Point2] = None
        self._load_started_at: Optional[float] = None
        self._unload_started_at: Optional[float] = None
        self.cloaked_counts: Dict[str, int] = {}
        self.form_counts: Dict[str, int] = {}
        self.skill_evidence: Dict[str, int] = {}
        self.peak_loaded_units: int = 0
        self.drop_unloaded: bool = False
        self.loaded_counts: Dict[str, int] = {}
        self.transport_activity = "support"
        self._transport_attempted = False
        self._unload_here = False
        self._target_clear_since: Optional[float] = None
        self._return_reason = "withdrawn"
        self._command_revision = -1
        self.retreat_method = "move"
        self.recall_status = None
        self.recall_failure = None
        self.recall_report = None
        self._recalled_tags = set()
        self._recall_home = None
        self._recall_cast_at = None
        self.creep_generating = 0
        self._creep_locked = set()

    def update_order(self, style: str, zone_id: str, withdrawing: bool, revision: int,
                     retreat_method: str = "move", recall_status: Optional[str] = None) -> None:
        """Change intent without rebinding members or erasing cargo ownership."""
        if revision == self._command_revision:
            return
        if retreat_method == "recall" and recall_status == "pending" and not withdrawing:
            self._command_revision = revision
            self.retreat_method = "recall"
            self.recall_status = "pending"
            self.recall_failure = None
            return
        self._command_revision = revision
        self.style, self.zone_id = style, zone_id
        self._hold_point = None
        self.failure_reason = None
        self._transport_attempted = False
        self._unload_here = False
        self._load_started_at = None
        self._unload_started_at = None
        self._target_clear_since = None
        self._return_reason = "withdrawn"
        self.transport_activity = "withdrawal" if withdrawing else "support"
        if withdrawing:
            self.phase = "withdrawing"
        elif self._bound:
            # Loaded survivors must unload at the new target, not be rebound.
            has_cargo = any(unit.tag in self._tags and getattr(unit, "cargo_used", 0)
                            for unit in self.ai.units)
            self.phase = "transit" if has_cargo else "fight"
            self._transport_attempted = has_cargo

    async def start(self, knowledge):
        await super().start(knowledge)
        from sc2bench_env.backends.sharpy.micro import build_combat_micro_rules

        self._micro_rules = build_combat_micro_rules()
        await self._micro_rules.start(knowledge)
        self._micro_started = True

    def _transport_contact(self, free_units) -> bool:
        from sc2bench_env.backends.sharpy.combat_styles import TRANSPORT_CONTACT_RADIUS
        return any(self._is_visible_enemy(enemy)
                   and any(enemy.distance_to(unit) <= TRANSPORT_CONTACT_RADIUS
                           and self._can_hit(enemy, unit) for unit in free_units)
                   for enemy in list(self.ai.enemy_units) + list(self.ai.enemy_structures))

    def _mission_carriers(self, free_units):
        types = set(_CARRIER_LOAD) | {UnitTypeId.WARPPRISMPHASING}
        return free_units.filter(lambda unit: unit.type_id in types)

    def _maybe_start_transport(self, free_units, target: Point2) -> bool:
        from sc2bench_env.backends.sharpy.combat_styles import TRANSPORT_MIN_TRAVEL
        if self._transport_attempted or self.phase != "fight" or self._transport_contact(free_units):
            return False
        carriers = self._mission_carriers(free_units)
        passengers = free_units.filter(self._is_passenger)
        if not carriers or not passengers or passengers.center.distance_to(target) <= TRANSPORT_MIN_TRAVEL:
            return False
        if not any(max(0, carrier.cargo_max - carrier.cargo_used) >= unit.cargo_size
                   for carrier in carriers for unit in passengers):
            return False
        self._transport_attempted = True
        self._load_started_at = None
        self.phase = "load"
        return True

    def _reserved_tags(self) -> set:
        reserved = getattr(self.ai, "bench_combat_tags", None)
        if reserved is None:
            reserved = set()
            self.ai.bench_combat_tags = reserved
        return reserved

    def _resolve_zone(self):
        registry = getattr(self.ai, "zone_registry", None)
        if registry is None:
            return None, None
        zone = registry.resolve_zone(self.zone_manager, self.zone_id)
        return registry, zone

    def _resolve_target(self) -> Optional[Point2]:
        registry, zone = self._resolve_zone()
        if zone is not None:
            center = zone.center_location
        elif registry is not None:
            center = registry.center_for(self.zone_id)
            center = Point2(center) if center is not None else None
        else:
            center = None
        if center is None:
            return None

        if self.style == "defend":
            self._hold_point = self._defend_station(zone, center)
            return self._hold_point
        return center

    @staticmethod
    def _zone_has_own_base(zone) -> bool:
        if zone is None:
            return False
        townhall = getattr(zone, "our_townhall", None)
        return (townhall is not None
                and bool(getattr(townhall, "is_ready", True))
                and not bool(getattr(townhall, "is_flying", False)))

    def _gather_point(self, zone, center: Point2) -> Point2:
        gather = getattr(zone, "gather_point", None) if zone is not None else None
        return gather if gather is not None else center

    def _expanding_to(self) -> Optional[Point2]:
        getter = getattr(getattr(self, "knowledge", None), "get_manager", None)
        if not callable(getter):
            return None
        try:
            from sharpy.interfaces import IGatherPointSolver
            solver = getter(IGatherPointSolver)
        except Exception:
            return None
        point = getattr(solver, "expanding_to", None)
        return point if isinstance(point, Point2) else None

    def _townhall_build_ordered_here(self, center: Point2) -> bool:
        workers = getattr(self.ai, "workers", None)
        if workers is None:
            workers = ()
        for worker in workers:
            for order in getattr(worker, "orders", ()) or ():
                ability = getattr(order, "ability", None)
                ability_id = getattr(ability, "id", ability)
                if ability_id not in _TOWNHALL_BUILD and ability not in _TOWNHALL_BUILD:
                    continue
                target = getattr(order, "target", None)
                point = getattr(target, "position", target)
                try:
                    if center.distance_to(point) <= 3:
                        return True
                except (AttributeError, TypeError):
                    continue
        return False

    def _base_is_being_built_here(self, zone, center: Point2) -> bool:
        townhall = getattr(zone, "our_townhall", None) if zone is not None else None
        if townhall is not None and not bool(getattr(townhall, "is_ready", True)):
            return True
        if self._townhall_build_ordered_here(center):
            return True
        expanding = self._expanding_to()
        return expanding is not None and expanding.distance_to(center) <= 3

    def _defend_station(self, zone, center: Point2) -> Point2:
        """Own base, or a base being placed here, keeps the old gather point.

        Otherwise the group holds the zone center.
        """
        if self._zone_has_own_base(zone) or self._base_is_being_built_here(zone, center):
            return self._gather_point(zone, center)
        return center

    def _defend_target(self, zone_center: Point2, zone) -> Point2:
        # Defense holds a stable safe point. Enemy movement must never rewrite
        # the objective and lure defenders across the map. MoveType.Hold handles
        # firing at units in range without pursuing them or attacking structures.
        return self._defend_station(zone, zone_center)

    def _configure_micro_boundary(self, zone) -> None:
        if self._micro_rules is None:
            return
        from sc2bench_env.backends.sharpy.combat_styles import (
    unit_available_for_background,
            defend_leash_radius,
        )
        self._micro_rules.boundary = None
        self._micro_rules.return_point = None
        self._micro_rules.hold_position = False
        if self.phase == "withdrawing":
            return
        if self.style == "defend":
            registry, _ = self._resolve_zone()
            center = zone.center_location if zone is not None else Point2(registry.center_for(self.zone_id))
            radius = float(getattr(zone, "radius", 15.0) or 15.0)
            leash = defend_leash_radius(radius)
            self._micro_rules.boundary = lambda position: position.distance_to(center) <= leash
            station = self._defend_station(zone, center)
            self._micro_rules.return_point = (station if station.distance_to(center) <= leash else center)
            self._micro_rules.hold_position = True

    @staticmethod
    def _is_visible_enemy(enemy) -> bool:
        return (bool(getattr(enemy, "is_visible", False))
                and not getattr(enemy, "is_snapshot", False)
                and not getattr(enemy, "is_memory", False)
                and not getattr(enemy, "is_hallucination", False)
                and bool(getattr(enemy, "is_ready", True)))

    def _can_hit(self, attacker, target) -> bool:
        from sc2bench_env.backends.sharpy.weapon_facts import has_active_weapon

        # Sharpy real_range handles flying targets, radii and unit-specific ranges.
        # Its spell-range overrides do not imply an active weapon.
        return has_active_weapon(attacker) and self.unit_values.real_range(attacker, target) > 0

    def _is_passenger(self, unit) -> bool:
        if getattr(unit, "is_flying", False) or getattr(unit, "is_structure", False):
            return False
        if unit.type_id in _CARRIER_LOAD or unit.type_id == UnitTypeId.WARPPRISMPHASING:
            return False
        return int(getattr(unit, "cargo_size", 0) or 0) > 0

    def _release_members(self, units) -> bool:
        from sc2.units import Units
        from sharpy.managers.core.roles import UnitTask

        tags = {int(unit.tag) for unit in units}
        if units:
            self.roles.set_tasks(UnitTask.Idle, Units(list(units), self.ai))
            getattr(self.ai, "bench_group0_tags", set()).update(tags)
        reserved = self._reserved_tags()
        for tag in tags:
            reserved.discard(tag)
        self._tags = [tag for tag in self._tags if int(tag) not in tags]
        self._recalled_tags -= tags
        if not self._tags:
            self.end_reason = "recalled"
            return True
        return False

    def _attempt_recall(self, free) -> None:
        from sc2bench_env.backends.sharpy.protoss_recall import (
            RECALL_ABILITY, group_center, recall_energy_cost, select_main_nexus, split_by_radius,
        )

        structures = self.ai.structures(UnitTypeId.NEXUS)
        nexus, reason = select_main_nexus(list(structures), self.ai.start_location)
        members = [unit for unit in free if not getattr(unit, "is_structure", False)]
        member_tags = [int(unit.tag) for unit in members]
        if nexus is None:
            self.recall_status = "failed"
            self.recall_failure = reason
            self.recall_report = {"failure": reason, "recalled": [], "not_recalled": member_tags}
            return
        cost = recall_energy_cost(getattr(self.ai, "_game_data", None))
        before = float(getattr(nexus, "energy", 0) or 0)
        if before < cost:
            self.recall_status = "failed"
            self.recall_failure = "nexus_energy"
            self.recall_report = {
                "failure": "nexus_energy", "energy_before": before, "energy_cost": cost,
                "recalled": [], "not_recalled": member_tags,
            }
            return
        center = group_center(members)
        if center is None:
            self.recall_status = "failed"
            self.recall_failure = "no_members"
            self.recall_report = {"failure": "no_members", "energy_before": before, "recalled": [], "not_recalled": []}
            return
        inside, outside = split_by_radius(members, center)
        if not nexus(RECALL_ABILITY, center):
            self.recall_status = "failed"
            self.recall_failure = "ability_unavailable"
            self.recall_report = {
                "failure": "ability_unavailable", "energy_before": before,
                "recalled": [], "not_recalled": member_tags,
            }
            return
        after = float(getattr(nexus, "energy", before) or before)
        self.recall_status = "cast"
        self.recall_failure = None
        self._recalled_tags = {int(unit.tag) for unit in inside}
        self._recall_home = nexus.position
        self._recall_cast_at = float(getattr(self.ai, "time", 0) or 0)
        self.recall_report = {
            "energy_before": before,
            "energy_after": after,
            "energy_cost": cost,
            "recalled": sorted(self._recalled_tags),
            "not_recalled": [int(unit.tag) for unit in outside],
        }
        if outside or not inside:
            self.phase = "withdrawing"

    def _sync_overlord_creep(self, units, target, hold_group: bool) -> None:
        from sc2bench_env.backends.sharpy.zerg_creep import is_creep_overlord, maintain_station_creep

        point = self._hold_point or target
        locked = set()
        generating = 0
        creep_tags = getattr(self.ai, "bench_creep_tags", set())
        for unit in units:
            if not is_creep_overlord(unit):
                continue
            stationary = (
                hold_group and point is not None and unit.distance_to(point) <= 6
                and int(getattr(unit, "cargo_used", 0) or 0) == 0
            )
            if maintain_station_creep(self.ai, unit, stationary) in {"holding", "toggled"}:
                locked.add(unit.tag)
            if int(unit.tag) in getattr(self.ai, "bench_creep_tags", creep_tags):
                generating += 1
        self._creep_locked = locked
        self.creep_generating = generating

    def _run_withdraw(self, free_units) -> bool:
        from sc2bench_env.backends.sharpy.combat_styles import PROVISIONAL_WITHDRAW_ARRIVAL
        from sharpy.interfaces.combat_manager import MoveType

        home = self._home_point()
        self._sync_overlord_creep(free_units, home, False)
        free_units = [unit for unit in free_units if unit.tag not in self._creep_locked and int(unit.tag) not in self._recalled_tags]
        if not free_units:
            return False
        self.transport_activity = "withdrawal"
        self._configure_micro_boundary(None)
        if all(unit.distance_to(home) <= PROVISIONAL_WITHDRAW_ARRIVAL for unit in free_units):
            # Do not release still-loaded infantry as inaccessible "free" units.
            loaded = [
                unit for unit in free_units
                if unit.type_id in _CARRIER_UNLOAD and int(getattr(unit, "cargo_used", 0) or 0) > 0
            ]
            if loaded:
                self.transport_activity = "unload"
                for carrier in loaded:
                    carrier(_CARRIER_UNLOAD[carrier.type_id], carrier.position)
                return False
            return self._release(self._return_reason)
        # Pick up troops that are still with the carrier. Do not fly back for
        # units already outside the local group.
        controlled = set()
        passengers = [unit for unit in free_units if self._is_passenger(unit)]
        carriers = [
            unit for unit in free_units
            if unit.type_id in _CARRIER_LOAD or unit.type_id == UnitTypeId.WARPPRISMPHASING
        ]
        for carrier in carriers:
            if carrier.distance_to(home) <= PROVISIONAL_WITHDRAW_ARRIVAL:
                continue
            if carrier.type_id == UnitTypeId.WARPPRISMPHASING:
                carrier(AbilityId.MORPH_WARPPRISMTRANSPORTMODE, None)
                controlled.add(carrier.tag)
                continue
            space = max(0, int(getattr(carrier, "cargo_max", 0) or 0) - int(getattr(carrier, "cargo_used", 0) or 0))
            choices = [
                unit for unit in passengers
                if unit.tag not in controlled
                and int(getattr(unit, "cargo_size", 0) or 0) <= space
                and carrier.distance_to(unit) <= _WITHDRAW_PICKUP_RADIUS
                and unit.distance_to(home) > PROVISIONAL_WITHDRAW_ARRIVAL
            ]
            if not choices:
                continue
            unit = min(choices, key=lambda unit: (
                getattr(unit, "health_percentage", 1), carrier.distance_to(unit), unit.tag,
            ))
            controlled.add(carrier.tag)
            if carrier.distance_to(unit) <= _LOAD_RADIUS:
                carrier(_CARRIER_LOAD[carrier.type_id], unit)
                controlled.add(unit.tag)
            else:
                carrier.move(unit.position)
        for unit in free_units:
            if unit.tag in controlled:
                continue
            self.combat.add_unit(unit)
        if controlled:
            self.transport_activity = "pickup"
        rules = self._micro_rules if self._micro_started else None
        if any(unit.tag not in controlled for unit in free_units):
            self.combat.execute(home, MoveType.DefensiveRetreat, rules)
        return False

    def _home_point(self) -> Point2:
        gather = getattr(self.ai, "bench_home_gather", None)
        if gather is not None and callable(getattr(gather, "home_point", None)):
            point = gather.home_point()
            if point is not None:
                return point
        return self.ai.start_location

    def _target_confirmed_clear(self, free_units, zone, target: Point2) -> bool:
        """Confirm an attack objective is clear before holding it as defend."""
        from sc2bench_env.backends.sharpy.combat_styles import TARGET_CLEAR_CONFIRM_SECONDS

        if self.style != "attack" or self.phase != "fight" or zone is None or not free_units.exists:
            self._target_clear_since = None
            return False
        radius = max(_ARRIVAL_RADIUS, float(getattr(zone, "radius", 15.0) or 15.0))
        positions = [unit.position for unit in free_units]
        center = Point2((
            sum(point.x for point in positions) / len(positions),
            sum(point.y for point in positions) / len(positions),
        ))
        if center.distance_to(target) > radius:
            self._target_clear_since = None
            return False
        is_visible = getattr(self.ai, "is_visible", None)
        if not callable(is_visible) or not is_visible(target):
            self._target_clear_since = None
            return False
        enemies = list(self.ai.enemy_units) + list(self.ai.enemy_structures)
        if any(self._is_visible_enemy(enemy) and enemy.distance_to(target) <= radius
               for enemy in enemies):
            self._target_clear_since = None
            return False
        now = float(self.ai.time)
        if self._target_clear_since is None:
            self._target_clear_since = now
            return False
        return now - self._target_clear_since >= TARGET_CLEAR_CONFIRM_SECONDS

    def _hold_cleared_objective(self) -> None:
        """Stay on the cleared zone as defend until a new order or retreat."""
        self.style = "defend"
        self.phase = "fight"
        self._target_clear_since = None
        self._return_reason = "withdrawn"

    def _own_adapter(self):
        adapter = getattr(self.ai, "adapter", None)
        if adapter is not None and hasattr(adapter, "train_unit_type"):
            return adapter
        from sc2bench_env.backends.sharpy.races.terran import TerranAdapter

        return TerranAdapter()

    def _unit_type_map(self) -> Dict[str, UnitTypeId]:
        adapter = self._own_adapter()
        mapping: Dict[str, UnitTypeId] = {}
        for name in self.units:
            unit_type = adapter.train_unit_type(name)
            if unit_type is not None:
                mapping[name] = unit_type
        return mapping

    def _platform_name(self, unit_type: UnitTypeId) -> Optional[str]:
        return self._own_adapter().normalize_unit_name(unit_type.name)

    def _bind_units(self) -> bool:
        from sc2bench_env.backends.sharpy.combat_styles import available_for_mission

        type_map = self._unit_type_map()
        reserved = self._reserved_tags()
        blocked = set(reserved)
        blocked.update(getattr(self.ai, "bench_morph_tags", set()) or ())
        selected: List[int] = []
        for name, need in self.units.items():
            unit_type = type_map.get(name)
            if unit_type is None:
                self.failure_reason = f"unsupported_unit:{name}"
                return False
            types = self._own_adapter().combat_forms(name)
            candidates = []
            for type_id in types:
                for unit in self.ai.units(type_id).ready:
                    if unit.tag in blocked or unit.is_structure:
                        continue
                    candidates.append(unit)
            group0 = getattr(self.ai, "bench_group0_tags", None)
            excluded = set(self._own_adapter().home_gather_excluded())
            idle = [
                unit
                for unit in candidates
                if available_for_mission(unit, self.roles, blocked)
                and (
                    group0 is None
                    or unit.tag in group0
                    or name in excluded
                )
            ]
            # Never fall back to stealing auto-defense / attack-owned units.
            pool = idle
            if len(pool) < need:
                available = len(pool)
                self.failure_reason = (
                    f"insufficient_units:{name}"
                    f":requested={int(need)}:available={available}:missing={int(need) - available}"
                )
                return False
            pool.sort(key=lambda unit: unit.tag)
            selected.extend(unit.tag for unit in pool[:need])
        self._tags = selected
        reserved.update(selected)
        getattr(self.ai, "bench_group0_tags", set()).difference_update(selected)
        self._bound = True
        return True

    def _passenger_tags(self, transporters) -> set:
        tags = set()
        for transport in transporters:
            for passenger in getattr(transport, "passengers", []) or []:
                tags.add(int(passenger.tag))
        return tags

    def _collect_mission_units(self):
        """Living free units plus cargo still owned by this mission."""
        from sc2.units import Units

        free = []
        cargo_tags = set()
        missing = []
        for tag in list(self._tags):
            unit = self.ai.units.find_by_tag(tag)
            if unit is not None and unit.is_ready:
                free.append(unit)
                for passenger in getattr(unit, "passengers", []) or []:
                    cargo_tags.add(int(passenger.tag))
                continue
            missing.append(tag)

        # Tags that vanished may be inside a mission carrier.
        still_owned = []
        carrier_types = tuple(_CARRIER_LOAD) + (UnitTypeId.WARPPRISMPHASING,)
        owned_tags = set(self._tags) | {unit.tag for unit in free}
        for tag in missing:
            if tag in cargo_tags:
                still_owned.append(tag)
                continue
            found = False
            for carrier_type in carrier_types:
                for carrier in self.ai.units(carrier_type):
                    if carrier.tag not in owned_tags:
                        continue
                    for passenger in getattr(carrier, "passengers", []) or []:
                        if int(passenger.tag) == int(tag):
                            still_owned.append(tag)
                            cargo_tags.add(int(tag))
                            found = True
                            break
                    if found:
                        break
                if found:
                    break

        # Transitional frames can expose a passenger in both ai.units and
        # carrier.passengers. Cargo stays owned, but has no on-map micro.
        cargo_tags.intersection_update(self._tags)
        free = [unit for unit in free if unit.tag not in cargo_tags]
        living_tags = {unit.tag for unit in free} | set(still_owned) | cargo_tags
        self._reserved_tags().difference_update(set(self._tags) - living_tags)
        self._tags = [unit.tag for unit in free] + sorted(living_tags - {unit.tag for unit in free})
        self.peak_loaded_units = max(self.peak_loaded_units, len(cargo_tags & living_tags))
        return Units(free, self.ai), cargo_tags

    def _refresh_alive_counts(self, free_units, cargo_tags: set) -> None:
        counts = {name: 0 for name in self.units}
        cloaked_counts: Dict[str, int] = {}
        form_counts: Dict[str, int] = {}
        loaded_counts: Dict[str, int] = {}
        counted = set()
        for unit in free_units:
            name = self._platform_name(unit.type_id)
            if name in counts:
                counts[name] += 1
                counted.add(unit.tag)
                if getattr(unit, "is_cloaked", False):
                    cloaked_counts[name] = cloaked_counts.get(name, 0) + 1
            form_key = _FORM_TYPE_KEYS.get(unit.type_id)
            if form_key is not None:
                form_counts[form_key] = form_counts.get(form_key, 0) + 1
            for passenger in getattr(unit, "passengers", []) or []:
                pname = self._platform_name(passenger.type_id)
                if int(passenger.tag) in cargo_tags and pname in counts and passenger.tag not in counted:
                    counts[pname] += 1
                    counted.add(passenger.tag)
                    loaded_counts[pname] = loaded_counts.get(pname, 0) + 1
        self.alive_counts = counts
        self.cloaked_counts = cloaked_counts
        self.form_counts = form_counts
        self.loaded_counts = loaded_counts
        self._refresh_skill_evidence(free_units)

    def _refresh_skill_evidence(self, free_units) -> None:
        """Accumulate observed casts/forms so E2E can assert real skill use."""
        evidence = dict(self.skill_evidence)
        for unit in free_units:
            for order in getattr(unit, "orders", []) or []:
                text = str(getattr(getattr(order, "ability", None), "id", "") or "")
                upper = text.upper()
                for hint, key in _SKILL_ORDER_HINTS:
                    if hint in upper:
                        evidence[key] = int(evidence.get(key, 0)) + 1
            form_key = _FORM_TYPE_KEYS.get(unit.type_id)
            if form_key is not None:
                evidence[form_key] = max(
                    int(evidence.get(form_key, 0)),
                    int(self.form_counts.get(form_key, 0)),
                )
            if getattr(unit, "is_cloaked", False):
                name = self._platform_name(unit.type_id)
                if name:
                    key = f"{name}_cloaked"
                    evidence[key] = max(int(evidence.get(key, 0)), 1)
        # Own Auto Turrets are Raven skill products, not train targets.
        turrets = getattr(self.ai, "units", None)
        if turrets is not None:
            try:
                count = turrets(UnitTypeId.AUTOTURRET).amount
                structures = getattr(self.ai, "structures", None)
                if structures is not None:
                    count += structures(UnitTypeId.AUTOTURRET).amount
            except Exception:
                count = 0
            if count:
                evidence["raven_turret"] = max(int(evidence.get("raven_turret", 0)), int(count))
        # Enemy Interference Matrix / Anti-Armor buffs prove Raven casts landed.
        try:
            from sc2.ids.buff_id import BuffId
            from sc2bench_env.backends.sharpy.compat import has_antiarmor_debuff
            version = getattr(getattr(getattr(self, "knowledge", None), "version_manager", None),
                              "full_version", "")
            for enemy in list(self.ai.enemy_units) + list(self.ai.enemy_structures):
                if not self._is_visible_enemy(enemy):
                    continue
                if enemy.has_buff(BuffId.LOCKON):
                    evidence["cyclone_lock_hit"] = int(evidence.get("cyclone_lock_hit", 0)) + 1
                if enemy.has_buff(BuffId.EMPDECLOAK):
                    evidence["ghost_emp_hit"] = int(evidence.get("ghost_emp_hit", 0)) + 1
                if enemy.has_buff(BuffId.RAVENSCRAMBLERMISSILE):
                    evidence["raven_matrix_hit"] = int(evidence.get("raven_matrix_hit", 0)) + 1
                if has_antiarmor_debuff(enemy, version):
                    evidence["raven_antiarmor_hit"] = int(evidence.get("raven_antiarmor_hit", 0)) + 1
        except Exception:
            pass
        self.skill_evidence = evidence

    def _release(self, reason: str) -> bool:
        from sharpy.managers.core.roles import UnitTask

        reserved = self._reserved_tags()
        free, _cargo = self._collect_mission_units()
        if free.exists:
            self.roles.set_tasks(UnitTask.Idle, free)
        for tag in list(self._tags):
            reserved.discard(tag)
        if reason in {"withdrawn", "target_cleared"}:
            getattr(self.ai, "bench_group0_tags", set()).update(unit.tag for unit in free)
        self._tags = []
        self.end_reason = reason
        return True

    def _run_load(self, free_units, target: Point2) -> None:
        from sc2bench_env.backends.sharpy.combat_styles import DROP_LOAD_TIMEOUT_SECONDS
        self.transport_activity = "load"

        carriers = self._mission_carriers(free_units)
        passengers = free_units.filter(self._is_passenger)
        if not carriers.exists:
            self.phase = "fight"
            return
        for carrier in carriers:
            if carrier.type_id == UnitTypeId.WARPPRISMPHASING:
                carrier(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
                return
        if not passengers.exists:
            self.phase = "transit"
            return
        now = float(self.ai.time)
        if self._load_started_at is None:
            self._load_started_at = now
        capacity = {unit.tag: max(0, unit.cargo_max - unit.cargo_used) for unit in carriers}
        loaded = any(unit.cargo_used > 0 for unit in carriers)
        if (not any(space >= unit.cargo_size for space in capacity.values() for unit in passengers)
                or now - self._load_started_at >= DROP_LOAD_TIMEOUT_SECONDS):
            self.phase = "transit" if loaded else "fight"
            return
        issued_load = set()
        for unit in sorted(passengers, key=lambda unit: unit.tag):
            transports = [transport for transport in carriers
                          if transport.type_id in _CARRIER_LOAD
                          and capacity[transport.tag] >= unit.cargo_size]
            if not transports:
                continue
            nearest = min(transports, key=lambda transport: transport.distance_to(unit))
            capacity[nearest.tag] -= unit.cargo_size
            if unit.distance_to(nearest) > _LOAD_RADIUS:
                unit.move(nearest.position)
            elif nearest.tag not in issued_load:
                nearest(_CARRIER_LOAD[nearest.type_id], unit)
                issued_load.add(nearest.tag)

    def _run_transit(self, free_units, target: Point2) -> None:
        from sc2bench_env.backends.sharpy.combat_styles import transport_drop_point
        self.transport_activity = "transit"

        carriers = self._mission_carriers(free_units).filter(lambda unit: unit.cargo_used > 0)
        if not carriers.exists:
            self.phase = "fight"
            return
        drop_point = target if self.style == "defend" else transport_drop_point(target, self.ai.start_location)
        if all(carrier.distance_to(drop_point) <= _ARRIVAL_RADIUS for carrier in carriers):
            self.phase = "unload"
            return
        for carrier in carriers:
            if carrier.type_id == UnitTypeId.WARPPRISMPHASING:
                carrier(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
            else:
                carrier.move(drop_point)

    def _run_unload(self, free_units, target: Point2) -> None:
        from sc2bench_env.backends.sharpy.combat_styles import DROP_UNLOAD_TIMEOUT_SECONDS, transport_drop_point
        self.transport_activity = "unload"

        carriers = self._mission_carriers(free_units)
        drop_point = target if self.style == "defend" else transport_drop_point(target, self.ai.start_location)
        still_loaded = False
        now = float(self.ai.time)
        if self._unload_started_at is None:
            self._unload_started_at = now
        for carrier in carriers:
            if carrier.type_id == UnitTypeId.WARPPRISMPHASING:
                carrier(AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
                still_loaded = True
                continue
            if int(getattr(carrier, "cargo_used", 0) or 0) > 0:
                still_loaded = True
                point = carrier.position if self._unload_here else drop_point
                ability = _CARRIER_UNLOAD.get(carrier.type_id)
                if carrier.distance_to(point) > _UNLOAD_RADIUS or ability is None:
                    carrier.move(point)
                else:
                    carrier(ability, point)
        if not still_loaded:
            self.drop_unloaded = self.peak_loaded_units > 0
            self.phase = "fight"
        elif now - self._unload_started_at >= DROP_UNLOAD_TIMEOUT_SECONDS:
            # Invalid/blocked drop terrain must not leave a permanent unload task.
            self.phase = "withdrawing"

    async def execute(self) -> bool:
        if self.end_reason or self.failure_reason:
            return True

        target = self._resolve_target()
        if target is None:
            self.failure_reason = f"invalid_zone:{self.zone_id}"
            return True

        if not self._bound:
            if not self._bind_units():
                return True

        free, cargo_tags = self._collect_mission_units()
        self._refresh_alive_counts(free, cargo_tags)
        if not free.exists and not cargo_tags:
            return self._release("force_destroyed")

        from sharpy.interfaces.combat_manager import MoveType
        from sharpy.managers.core.roles import UnitTask

        if free.exists:
            # Reserved so PlanZoneDefense / get_defenders cannot yank mission units.
            self.roles.set_tasks(UnitTask.Reserved, free)

        if self._recalled_tags and self._recall_home is not None:
            arrived = [
                unit for unit in free
                if int(unit.tag) in self._recalled_tags and unit.distance_to(self._recall_home) <= 8
            ]
            if arrived and self._release_members(arrived):
                return True
            if (self._recall_cast_at is not None and float(self.ai.time) - float(self._recall_cast_at) > 2
                    and any(int(unit.tag) in self._recalled_tags for unit in free)):
                self._recalled_tags.clear()
                self.phase = "withdrawing"
            free, cargo_tags = self._collect_mission_units()

        hold_creep = self.style == "defend" and self.phase not in {"load", "transit", "unload", "withdrawing"}
        self._sync_overlord_creep(free, target, hold_creep)

        if self.recall_status == "pending" and self.retreat_method == "recall" and self.phase != "withdrawing":
            self._attempt_recall(free)
            if self.end_reason:
                return True

        if self.phase == "withdrawing":
            return self._run_withdraw(free)

        _, zone = self._resolve_zone()
        if self.style == "defend" and zone is not None:
            zone_center = zone.center_location
            target = self._defend_target(zone_center, zone)
        self._configure_micro_boundary(zone)

        if self._target_confirmed_clear(free, zone, target):
            self._hold_cleared_objective()
            if zone is not None:
                target = self._defend_target(zone.center_location, zone)
            self._configure_micro_boundary(zone)

        # No style-selected drops. Decide from current owned units and contact.
        # A delayed LOAD result must not leave cargo trapped in heal/escort.
        if self.phase == "fight" and any(unit.cargo_used > 0 for unit in self._mission_carriers(free)):
            self.phase = "transit"
            self._transport_attempted = True
        self._maybe_start_transport(free, target)
        transport_phase = self.phase
        if transport_phase in {"load", "transit"} and self._transport_contact(free):
            self.phase = "unload" if any(unit.cargo_used > 0 for unit in self._mission_carriers(free)) else "fight"
            self._unload_here = True
            self._unload_started_at = None
        transport_phase = self.phase
        if transport_phase == "load":
            self._run_load(free, target)
        elif transport_phase == "transit":
            self._run_transit(free, target)
        elif transport_phase == "unload":
            self._run_unload(free, target)
        if transport_phase in {"load", "transit", "unload"}:
            # Ground leftovers and other unit types continue moving/fighting.
            # Exclude explicit transport commands so combat micro cannot
            # overwrite LOAD/UNLOAD/MOVE in the same frame.
            if transport_phase == "load":
                support = free.filter(lambda unit: unit.type_id not in _CARRIER_LOAD
                                      and unit.type_id != UnitTypeId.WARPPRISMPHASING
                                      and not self._is_passenger(unit))
            else:
                support = free.filter(lambda unit: unit.type_id not in _CARRIER_LOAD
                                      or unit.cargo_used == 0)
            if support.exists:
                await self._drive_combat(support, target, MoveType.Assault)
            return False

        from sc2bench_env.backends.sharpy.combat_styles import style_move_type_name

        move_name = style_move_type_name(self.style)
        move_type = getattr(MoveType, move_name, MoveType.Assault)
        self.transport_activity = "support"
        await self._drive_combat(free, target, move_type)
        return False

    def _army_in_contact(self, units) -> bool:
        """A visible enemy near the army, or already in weapon range, ends the march leash."""
        from sc2bench_env.backends.sharpy.combat_styles import MARCH_CONTACT_RADIUS

        enemies = [
            enemy for enemy in list(self.ai.enemy_units) + list(self.ai.enemy_structures)
            if self._is_visible_enemy(enemy)
        ]
        if not enemies:
            return False
        for unit in units:
            if getattr(unit, "is_structure", False):
                continue
            for enemy in enemies:
                try:
                    distance = float(unit.distance_to(enemy))
                except (AttributeError, TypeError, ValueError):
                    continue
                if distance <= MARCH_CONTACT_RADIUS:
                    return True
                if not (self._can_hit(unit, enemy) or self._can_hit(enemy, unit)):
                    continue
                attacker, target = (unit, enemy) if self._can_hit(unit, enemy) else (enemy, unit)
                try:
                    reach = float(self.unit_values.real_range(attacker, target))
                except (AttributeError, TypeError, ValueError):
                    reach = 0.0
                if reach > 0 and distance <= reach + 0.5:
                    return True
        return False

    def _units_to_keep_with_army(self, units, target):
        """Fast attackers wait just ahead of the slower body instead of stringing out."""
        from sc2bench_env.backends.sharpy.combat_styles import march_formation

        if self.style != "attack":
            return [], None
        ahead, hold, _rear = march_formation(units, target)
        if not ahead or hold is None or self._army_in_contact(units):
            return [], None
        return ahead, hold

    async def _drive_combat(self, units, target, move_type):
        ahead, hold = [], None
        if getattr(move_type, "name", "") == "Assault":
            ahead, hold = self._units_to_keep_with_army(units, target)
        held = {unit.tag for unit in ahead}
        locked = set(getattr(self, "_creep_locked", set()))
        for unit in units:
            if unit.tag in locked or unit.tag in held:
                continue
            self.combat.add_unit(unit)
        rules = self._micro_rules if self._micro_started else None
        raven_micro = rules.unit_micros.get(UnitTypeId.RAVEN) if rules is not None else None
        if raven_micro is not None and hasattr(raven_micro, "prepare"):
            await raven_micro.prepare(units, self.ai)
        if any(unit.tag not in held and unit.tag not in locked for unit in units):
            self.combat.execute(target, move_type, rules)
        if hold is not None:
            for unit in ahead:
                if unit.tag not in locked:
                    unit.move(hold)
