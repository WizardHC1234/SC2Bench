"""Instant / ability-style Sharpy acts used by the platform adapter."""

from __future__ import annotations

from math import isfinite
from typing import Dict, List, Mapping, Optional, Sequence

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.plans.acts import ActBase

from sc2bench_env.interface.action_catalog import get_target
from sc2bench_env.interface.scouting import ScoutRoute, order_expansions

_MULE_ENERGY = float(get_target("call_mule", race="terran").energy)
_SCAN_ENERGY = float(get_target("scan", race="terran").energy)


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
            self.failure_reason = f"not_command_center:{self.structure_id}"
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
            scout.move(target)
            return False

        self._done = True
        self.current_zone = None
        return True


_GROUND_CARGO = frozenset({"marine", "marauder"})
_LOAD_RADIUS = 5.0
_UNLOAD_RADIUS = 4.0
_ARRIVAL_RADIUS = 12.0

# Alternate forms keyed for combat evidence / Observation.forms.
_FORM_TYPE_KEYS = {
    UnitTypeId.WIDOWMINEBURROWED: "widow_mine_burrowed",
    UnitTypeId.LIBERATORAG: "liberator_ag",
    UnitTypeId.VIKINGASSAULT: "viking_assault",
    UnitTypeId.THORAP: "thor_ap",
    UnitTypeId.SIEGETANKSIEGED: "siege_tank_sieged",
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
)


class ActCombatMission(ActBase):
    """Bind home army and execute attack/defend with backend transport micro.

    Stim / Tank / heal come from MicroRules. Banshee cloak is platform micro.
    Transport is a local execution decision, not an Agent-selected style.
    Passengers stay mission-owned and count toward alive composition.

    Geometry and local-power ratios use first-edition engineering defaults;
    see combat_styles.py. They are not empirically optimal values.
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
        self._below_ratio_since: Optional[float] = None
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
        self._withdraw_pickup_started = None
        self._command_revision = -1

    def update_order(self, style: str, zone_id: str, withdrawing: bool, revision: int) -> None:
        """Change intent without rebinding members or erasing cargo ownership."""
        if revision == self._command_revision:
            return
        self._command_revision = revision
        self.style, self.zone_id = style, zone_id
        self._hold_point = None
        self._below_ratio_since = None
        self.failure_reason = None
        self._transport_attempted = False
        self._unload_here = False
        self._load_started_at = None
        self._unload_started_at = None
        self._withdraw_pickup_started = None
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

    def _maybe_start_transport(self, free_units, target: Point2) -> bool:
        from sc2bench_env.backends.sharpy.combat_styles import TRANSPORT_MIN_TRAVEL
        if self._transport_attempted or self.phase != "fight" or self._transport_contact(free_units):
            return False
        medivacs = free_units.of_type(UnitTypeId.MEDIVAC)
        infantry = free_units.filter(lambda unit: self._platform_name(unit.type_id) in _GROUND_CARGO)
        if not medivacs or not infantry or infantry.center.distance_to(target) <= TRANSPORT_MIN_TRAVEL:
            return False
        if not any(max(0, med.cargo_max - med.cargo_used) >= unit.cargo_size
                   for med in medivacs for unit in infantry):
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
            gather = getattr(zone, "gather_point", None) if zone is not None else None
            self._hold_point = gather or center
            return self._hold_point
        return center

    def _defend_target(self, zone_center: Point2, zone) -> Point2:
        from sc2bench_env.backends.sharpy.combat_styles import defend_engage_target

        gather = getattr(zone, "gather_point", None) if zone is not None else None
        radius = float(getattr(zone, "radius", 15.0) or 15.0) if zone is not None else 15.0
        enemies = self.ai.enemy_units.closer_than(radius * 2.5, zone_center).filter(
            lambda enemy: self._is_visible_enemy(enemy)
        )
        enemy_pos = None
        if enemies.exists:
            enemy_pos = enemies.closest_to(zone_center).position
        target, _chasing = defend_engage_target(
            zone_center, radius, enemy_pos, gather or zone_center
        )
        return target

    def _configure_micro_boundary(self, zone) -> None:
        if self._micro_rules is None:
            return
        from sc2bench_env.backends.sharpy.combat_styles import (
            defend_leash_radius,
        )
        self._micro_rules.boundary = None
        self._micro_rules.return_point = None
        if self.phase == "withdrawing":
            return
        if self.style == "defend":
            registry, _ = self._resolve_zone()
            center = zone.center_location if zone is not None else Point2(registry.center_for(self.zone_id))
            radius = float(getattr(zone, "radius", 15.0) or 15.0)
            leash = defend_leash_radius(radius)
            self._micro_rules.boundary = lambda position: position.distance_to(center) <= leash
            gather = getattr(zone, "gather_point", None)
            self._micro_rules.return_point = (gather if gather is not None
                                             and gather.distance_to(center) <= leash else center)

    def _local_power_ratio(self, free_units, around: Point2) -> float:
        from sc2bench_env.backends.sharpy.combat_styles import (
            PROVISIONAL_LOCAL_BATTLE_RADIUS,
        )
        radius = PROVISIONAL_LOCAL_BATTLE_RADIUS
        local_own = [unit for unit in free_units if unit.distance_to(around) <= radius]
        # should_attack answers whether an OWN unit should join an attack, not
        # whether an enemy can threaten this composition. Include visible static
        # defense and armed workers, but never snapshot/memory enemies.
        enemies = list(self.ai.enemy_units) + list(self.ai.enemy_structures)
        threats = [enemy for enemy in enemies
                   if self._is_visible_enemy(enemy)
                   and enemy.distance_to(around) <= radius
                   and any(self._can_hit(enemy, unit) for unit in local_own)]
        if not threats:
            return float("inf")
        foe_power = sum(self.unit_values.power(enemy) for enemy in threats)
        if foe_power <= 1e-6:
            return float("inf")
        own_power = sum(self.unit_values.power(unit) for unit in local_own
                        if any(self._can_hit(unit, enemy) for enemy in threats))
        return float(own_power) / float(foe_power)

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

    def _maybe_start_withdraw(self, free_units, around: Point2) -> bool:
        from sc2bench_env.backends.sharpy.combat_styles import (
            PROVISIONAL_RETREAT_CONFIRM_SECONDS,
            PROVISIONAL_RETREAT_RATIO,
        )

        if self.style != "attack":
            return False
        threshold = PROVISIONAL_RETREAT_RATIO.get(self.style)
        if threshold is None:
            return False
        ratio = self._local_power_ratio(free_units, around)
        now = float(self.ai.time)
        if ratio < threshold:
            if self._below_ratio_since is None:
                self._below_ratio_since = now
            elif now - self._below_ratio_since >= PROVISIONAL_RETREAT_CONFIRM_SECONDS:
                self.phase = "withdrawing"
                self._below_ratio_since = None
                return True
        else:
            self._below_ratio_since = None
        return False

    def _run_withdraw(self, free_units) -> bool:
        from sc2bench_env.backends.sharpy.combat_styles import (
            PROVISIONAL_WITHDRAW_ARRIVAL, WITHDRAW_PICKUP_SECONDS, WITHDRAW_WOUNDED_HEALTH,
        )
        from sharpy.interfaces.combat_manager import MoveType

        home = self.ai.start_location
        self.transport_activity = "withdrawal"
        self._configure_micro_boundary(None)
        if all(unit.distance_to(home) <= PROVISIONAL_WITHDRAW_ARRIVAL for unit in free_units):
            # Do not release still-loaded infantry as inaccessible "free" units.
            loaded = free_units.of_type(UnitTypeId.MEDIVAC).filter(
                lambda unit: int(getattr(unit, "cargo_used", 0) or 0) > 0
            )
            if loaded.exists:
                self.transport_activity = "unload"
                for medivac in loaded:
                    medivac(AbilityId.UNLOADALLAT_MEDIVAC, medivac.position)
                return False
            return self._release("withdrawn")
        # Bounded local pickup: never march back to gather distant troops or
        # delay withdrawal indefinitely. Loaded carriers leave immediately.
        now = float(self.ai.time)
        if self._withdraw_pickup_started is None:
            self._withdraw_pickup_started = now
        controlled = set()
        capacity = {med.tag: max(0, med.cargo_max - med.cargo_used)
                    for med in free_units.of_type(UnitTypeId.MEDIVAC) if med.cargo_used == 0}
        contact = self._transport_contact(free_units)
        if now - self._withdraw_pickup_started < WITHDRAW_PICKUP_SECONDS:
            infantry = free_units.filter(lambda unit: self._platform_name(unit.type_id) in _GROUND_CARGO)
            for unit in sorted(infantry, key=lambda unit: (getattr(unit, "health_percentage", 1), unit.tag)):
                if contact and getattr(unit, "health_percentage", 1) > WITHDRAW_WOUNDED_HEALTH:
                    continue
                choices = [med for med in free_units.of_type(UnitTypeId.MEDIVAC)
                           if med.tag not in controlled and capacity.get(med.tag, 0) >= unit.cargo_size
                           and med.distance_to(unit) <= _LOAD_RADIUS
                           and med.distance_to(home) > PROVISIONAL_WITHDRAW_ARRIVAL]
                if choices:
                    med = min(choices, key=lambda med: med.distance_to(unit))
                    med(AbilityId.LOAD_MEDIVAC, unit)
                    capacity[med.tag] -= unit.cargo_size
                    controlled.update((med.tag, unit.tag))
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

    def _unit_type_map(self) -> Dict[str, UnitTypeId]:
        from sc2bench_env.backends.sharpy.races.terran import UNITS

        mapping: Dict[str, UnitTypeId] = {}
        for name in self.units:
            pair = UNITS.get(name)
            if pair is not None:
                mapping[name] = pair[0]
        return mapping

    def _platform_name(self, unit_type: UnitTypeId) -> Optional[str]:
        from sc2bench_env.backends.sharpy.races.terran import TerranAdapter

        return TerranAdapter().normalize_unit_name(unit_type.name)

    def _bind_units(self) -> bool:
        from sc2bench_env.backends.sharpy.combat_styles import available_for_mission

        type_map = self._unit_type_map()
        reserved = self._reserved_tags()
        selected: List[int] = []
        for name, need in self.units.items():
            unit_type = type_map.get(name)
            if unit_type is None:
                self.failure_reason = f"unsupported_unit:{name}"
                return False
            from sc2bench_env.backends.sharpy.races.terran import combat_unit_types
            types = combat_unit_types(name)
            candidates = []
            for type_id in types:
                for unit in self.ai.units(type_id).ready:
                    if unit.tag in reserved or unit.is_structure:
                        continue
                    candidates.append(unit)
            idle = [
                unit
                for unit in candidates
                if available_for_mission(unit, self.roles, reserved)
                and unit.tag in getattr(self.ai, "bench_group0_tags", {u.tag for u in candidates})
            ]
            # Never fall back to stealing auto-defense / attack-owned units.
            pool = idle
            if len(pool) < need:
                self.failure_reason = f"insufficient_units:{name}"
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

        # Tags that vanished may be inside a mission medivac.
        still_owned = []
        for tag in missing:
            if tag in cargo_tags:
                still_owned.append(tag)
                continue
            # Search all own medivacs for this passenger tag.
            found = False
            for medivac in self.ai.units(UnitTypeId.MEDIVAC):
                if medivac.tag not in self._tags and medivac.tag not in {
                    u.tag for u in free
                }:
                    continue
                for passenger in getattr(medivac, "passengers", []) or []:
                    if int(passenger.tag) == int(tag):
                        still_owned.append(tag)
                        cargo_tags.add(int(tag))
                        found = True
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
        if reason == "withdrawn":
            getattr(self.ai, "bench_group0_tags", set()).update(unit.tag for unit in free)
        self._tags = []
        self.end_reason = reason
        return True

    def _run_load(self, free_units, target: Point2) -> None:
        from sc2bench_env.backends.sharpy.combat_styles import DROP_LOAD_TIMEOUT_SECONDS
        self.transport_activity = "load"

        medivacs = free_units.of_type(UnitTypeId.MEDIVAC)
        infantry = free_units.filter(
            lambda unit: self._platform_name(unit.type_id) in _GROUND_CARGO
        )
        if not medivacs.exists:
            self.phase = "fight"
            return
        if not infantry.exists:
            # Either loaded or composition has no free ground left outside.
            self.phase = "transit"
            return
        now = float(self.ai.time)
        if self._load_started_at is None:
            self._load_started_at = now
        capacity = {unit.tag: max(0, unit.cargo_max - unit.cargo_used) for unit in medivacs}
        loaded = any(unit.cargo_used > 0 for unit in medivacs)
        # Oversized forces travel with loaded infantry plus ground leftovers;
        # if nothing can load, use ordinary ground/support combat instead.
        if (not any(space >= unit.cargo_size for space in capacity.values() for unit in infantry)
                or now - self._load_started_at >= DROP_LOAD_TIMEOUT_SECONDS):
            self.phase = "transit" if loaded else "fight"
            return
        issued_load = set()
        for unit in sorted(infantry, key=lambda unit: unit.tag):
            transports = [transport for transport in medivacs
                          if capacity[transport.tag] >= unit.cargo_size]
            if not transports:
                continue
            nearest = min(transports, key=lambda transport: transport.distance_to(unit))
            capacity[nearest.tag] -= unit.cargo_size
            if unit.distance_to(nearest) > _LOAD_RADIUS:
                unit.move(nearest.position)
            elif nearest.tag not in issued_load:
                # One load command per transporter per frame; later commands
                # must not overwrite an earlier passenger order.
                nearest(AbilityId.LOAD_MEDIVAC, unit)
                issued_load.add(nearest.tag)

    def _run_transit(self, free_units, target: Point2) -> None:
        from sc2bench_env.backends.sharpy.combat_styles import transport_drop_point
        self.transport_activity = "transit"

        medivacs = free_units.of_type(UnitTypeId.MEDIVAC).filter(lambda unit: unit.cargo_used > 0)
        if not medivacs.exists:
            self.phase = "fight"
            return
        # Unload short of the zone center so ground does not dump into the middle.
        drop_point = target if self.style == "defend" else transport_drop_point(target, self.ai.start_location)
        if all(medivac.distance_to(drop_point) <= _ARRIVAL_RADIUS for medivac in medivacs):
            self.phase = "unload"
            return
        for medivac in medivacs:
            medivac.move(drop_point)

    def _run_unload(self, free_units, target: Point2) -> None:
        from sc2bench_env.backends.sharpy.combat_styles import DROP_UNLOAD_TIMEOUT_SECONDS, transport_drop_point
        self.transport_activity = "unload"

        medivacs = free_units.of_type(UnitTypeId.MEDIVAC)
        drop_point = target if self.style == "defend" else transport_drop_point(target, self.ai.start_location)
        still_loaded = False
        now = float(self.ai.time)
        if self._unload_started_at is None:
            self._unload_started_at = now
        for medivac in medivacs:
            if int(getattr(medivac, "cargo_used", 0) or 0) > 0:
                still_loaded = True
                point = medivac.position if self._unload_here else drop_point
                if medivac.distance_to(point) > _UNLOAD_RADIUS:
                    medivac.move(point)
                else:
                    medivac(AbilityId.UNLOADALLAT_MEDIVAC, point)
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

        if self.phase == "withdrawing":
            return self._run_withdraw(free)

        focus = free.center if free.exists else target
        if self.phase == "fight" and free.exists and not cargo_tags:
            if self._maybe_start_withdraw(free, focus):
                return self._run_withdraw(free)

        _, zone = self._resolve_zone()
        if self.style == "defend" and zone is not None:
            zone_center = zone.center_location
            target = self._defend_target(zone_center, zone)
        self._configure_micro_boundary(zone)

        # No style-selected drops. Decide from current owned units and contact.
        # A delayed LOAD result must not leave cargo trapped in heal/escort.
        if self.phase == "fight" and any(unit.cargo_used > 0 for unit in free.of_type(UnitTypeId.MEDIVAC)):
            self.phase = "transit"
            self._transport_attempted = True
        self._maybe_start_transport(free, target)
        transport_phase = self.phase
        if transport_phase in {"load", "transit"} and self._transport_contact(free):
            self.phase = "unload" if any(unit.cargo_used > 0 for unit in free.of_type(UnitTypeId.MEDIVAC)) else "fight"
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
                support = free.filter(lambda unit: unit.type_id != UnitTypeId.MEDIVAC
                                      and self._platform_name(unit.type_id) not in _GROUND_CARGO)
            else:
                support = free.filter(lambda unit: unit.type_id != UnitTypeId.MEDIVAC or unit.cargo_used == 0)
            if support.exists:
                await self._drive_combat(support, target, MoveType.Assault)
            return False

        from sc2bench_env.backends.sharpy.combat_styles import style_move_type_name

        move_name = style_move_type_name(self.style)
        move_type = getattr(MoveType, move_name, MoveType.Assault)
        self.transport_activity = "support"
        await self._drive_combat(free, target, move_type)
        return False

    async def _drive_combat(self, units, target, move_type):
        for unit in units:
            self.combat.add_unit(unit)
        rules = self._micro_rules if self._micro_started else None
        raven_micro = rules.unit_micros.get(UnitTypeId.RAVEN) if rules is not None else None
        if raven_micro is not None and hasattr(raven_micro, "prepare"):
            await raven_micro.prepare(units, self.ai)
        self.combat.execute(target, move_type, rules)
