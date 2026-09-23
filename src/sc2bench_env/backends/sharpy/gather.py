"""Home group_0 gathering/defense, without automatic offensive reinforcement.

Adapted from Commander's production rallies and Sharpy's idle gathering.
The completed natural is the forward home point; the main is the fallback.
"""

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts import ActBase

from sc2bench_env.backends.sharpy.combat_styles import available_for_mission
from sc2bench_env.interface.action_catalog import get_target
from sc2bench_env.interface.observations import WORKER_UNIT_NAMES

HOME_GATHER_RADIUS = 6.5
HOME_DEFEND_RADIUS = 20.0
PRODUCTION_TYPES = frozenset({UnitTypeId.BARRACKS, UnitTypeId.FACTORY, UnitTypeId.STARPORT})
MOBILE_FORM_ABILITIES = {
    UnitTypeId.SIEGETANKSIEGED: AbilityId.UNSIEGE_UNSIEGE,
    UnitTypeId.WIDOWMINEBURROWED: AbilityId.BURROWUP_WIDOWMINE,
    UnitTypeId.LIBERATORAG: AbilityId.MORPH_LIBERATORAAMODE,
}
# Economy casts are explicit orders. Home gathering must not replace them.
_KEEP_ORDER = {
    AbilityId.EFFECT_INJECTLARVA,
    AbilityId.BUILD_CREEPTUMOR,
    AbilityId.BUILD_CREEPTUMOR_QUEEN,
    AbilityId.BUILD_CREEPTUMOR_TUMOR,
    AbilityId.ZERGBUILD_CREEPTUMOR,
    AbilityId.MORPHZERGLINGTOBANELING_BANELING,
    AbilityId.MORPHTORAVAGER_RAVAGER,
    AbilityId.MORPH_LURKER,
    AbilityId.MORPH_OVERSEER,
    AbilityId.MORPH_OVERLORDTRANSPORT,
    AbilityId.MORPHTOBROODLORD_BROODLORD,
}


def _kept_order(unit) -> bool:
    for order in getattr(unit, "orders", ()) or ():
        ability = getattr(order, "ability", None)
        if getattr(ability, "id", None) in _KEEP_ORDER:
            return True
    return False


class PlanHomeGather(ActBase):
    def __init__(self, adapter):
        super().__init__()
        self.adapter = adapter
        self._rallied_tags = set()
        self._last_point = None
        self._micro_started = False

    async def start(self, knowledge):
        await super().start(knowledge)
        from sc2bench_env.backends.sharpy.micro import build_combat_micro_rules
        self._micro_rules = build_combat_micro_rules()
        await self._micro_rules.start(knowledge)
        self._micro_started = True
        self.ai.bench_group0_tags = set()
        self.ai.bench_home_gather = self

    def _bunker_tags(self):
        return set(getattr(self.ai, "bench_bunker_tags", set()) or ())

    def pool_units(self):
        tags = getattr(self.ai, "bench_group0_tags", set())
        reserved = getattr(self.ai, "bench_combat_tags", set())
        bunker = self._bunker_tags()
        return [u for u in self.ai.units if u.tag in tags and u.tag not in reserved and u.tag not in bunker
                and u.is_ready and not u.is_structure and not getattr(u, "is_hallucination", False)
                and not _kept_order(u)]

    def home_point(self):
        """Completed natural gather point, else own-main side of the ramp."""
        start = self.ai.start_location
        natural = getattr(self.zone_manager, "own_natural", None)
        townhall = getattr(natural, "our_townhall", None)
        if (natural is not None and townhall is not None
                and bool(getattr(townhall, "is_ready", False))
                and not bool(getattr(townhall, "is_flying", False))):
            point = (getattr(natural, "gather_point", None)
                     or getattr(natural, "center_location", None)
                     or getattr(townhall, "position", None))
            pathable = getattr(self.ai, "in_pathing_grid", None)
            if point is not None and (pathable is None or pathable(point)):
                return point
        main = getattr(self.zone_manager, "own_main_zone", None)
        ramp = getattr(main, "ramp", None)
        if ramp is not None:
            point = ramp.top_center.towards(ramp.bottom_center, -4)
        else:
            center = getattr(getattr(self.ai, "game_info", None), "map_center", None)
            point = start.towards(center, min(8, start.distance_to(center))) if center is not None else start
        pathable = getattr(self.ai, "in_pathing_grid", None)
        return point if pathable is None or pathable(point) else start

    def eligible(self, unit):
        name = self.adapter.normalize_unit_name(unit.type_id.name)
        spec = get_target(name, race=self.adapter.race_name) if name is not None else None
        return (
            spec is not None and spec.action == "train" and name not in WORKER_UNIT_NAMES
            and available_for_mission(unit, self.roles, getattr(self.ai, "bench_combat_tags", set()))
            and unit.tag not in self._bunker_tags()
            and not getattr(unit, "cargo_used", 0)
            and bool(getattr(unit, "is_idle", False))
        )

    async def execute(self):
        point = self.home_point()
        self.ai.bench_group0_home_point = point
        tags = getattr(self.ai, "bench_group0_tags", None)
        if tags is None:
            tags = set()
            self.ai.bench_group0_tags = tags
        tags.intersection_update(u.tag for u in self.ai.units if u.is_ready)
        tags.difference_update(getattr(self.ai, "bench_combat_tags", set()))
        tags.difference_update(self._bunker_tags())
        for unit in self.ai.units:
            if self.eligible(unit) and unit.distance_to(point) <= HOME_GATHER_RADIUS:
                tags.add(unit.tag)
        if self._last_point is None or self._last_point.distance_to(point) > 1:
            self._rallied_tags.clear()
            self._last_point = point
        producers = [b for b in self.ai.structures
                     if b.type_id in PRODUCTION_TYPES and b.is_ready and not b.is_flying]
        self._rallied_tags.intersection_update(b.tag for b in producers)
        for building in producers:
            if building.tag not in self._rallied_tags:
                building(AbilityId.RALLY_BUILDING, point)
                self._rallied_tags.add(building.tag)
        used = set(getattr(self.ai, "unit_tags_received_action", ()) or ())
        for unit in self.ai.units:
            if unit.tag in tags or unit.tag in used or _kept_order(unit):
                continue
            if not self.eligible(unit) or unit.distance_to(point) <= HOME_GATHER_RADIUS:
                continue
            mobile = MOBILE_FORM_ABILITIES.get(unit.type_id)
            if mobile is not None:
                unit(mobile)
            else:
                # Move, not attack-move. Defense retains its existing authority.
                # Arrival, not this movement order, admits them to group_0.
                unit.move(point)
        if self._micro_started:
            from sc2.units import Units
            from sharpy.managers.core.roles import UnitTask
            from sharpy.interfaces.combat_manager import MoveType
            members = Units(
                [unit for unit in self.pool_units() if unit.tag not in used],
                self.ai,
            )
            if members.exists:
                # Reserved from unrelated auto-defense, but explicitly assignable
                # through group_0. Never register them as outbound mission tags.
                self.roles.set_tasks(UnitTask.Reserved, members)
                enemies = [u for u in self.ai.all_enemy_units
                           if u.distance_to(point) <= HOME_DEFEND_RADIUS
                           and getattr(u, "is_visible", False)
                           and not getattr(u, "is_memory", False)
                           and not getattr(u, "is_snapshot", False)]
                target = min(enemies, key=lambda u: u.distance_to(point)).position if enemies else point
                self._micro_rules.boundary = lambda p: p.distance_to(point) <= HOME_DEFEND_RADIUS
                self._micro_rules.return_point = point
                self.ai.bench_group0_engaged = bool(enemies)
                for unit in members:
                    self.combat.add_unit(unit)
                self.combat.execute(target, MoveType.Assault, self._micro_rules)
            else:
                self.ai.bench_group0_engaged = False
        return True
