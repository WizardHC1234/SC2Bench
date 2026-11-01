from typing import TYPE_CHECKING, Optional

from sc2.data import Race
from sc2.ids.ability_id import AbilityId
from sharpy.general.zone import Zone
from sharpy.managers.core.roles import UnitTask
from sharpy.managers.core import ZoneManager, UnitRoleManager
from sharpy.interfaces import IIncomeCalculator, IGatherPointSolver
from sc2.ids.unit_typeid import UnitTypeId
from sc2.unit import Unit, UnitOrder
from .act_base import ActBase
from .build_location_recovery import BuildLocationRecovery
from sharpy.sc2math import to_new_ticks

if TYPE_CHECKING:
    from sharpy.knowledges import *


def get_new_townhall_type(race: Race):
    if race == Race.Zerg:
        return UnitTypeId.HATCHERY
    elif race == Race.Protoss:
        return UnitTypeId.NEXUS
    elif race == Race.Terran:
        return UnitTypeId.COMMANDCENTER


train_worker_abilitites = {AbilityId.NEXUSTRAIN_PROBE, AbilityId.COMMANDCENTERTRAIN_SCV}


class Expand(BuildLocationRecovery, ActBase):
    gather_manager: IGatherPointSolver
    zone_manager: ZoneManager
    income_calculator: IIncomeCalculator
    roles: UnitRoleManager

    def __init__(
        self,
        to_count: int,
        priority: bool = False,
        consider_worker_production: bool = True,
        priority_base_index: Optional[int] = None,
    ):
        assert isinstance(to_count, int)
        self.priority_base_index = priority_base_index
        self.to_count: int = to_count
        self.builder_tag: Optional[int] = None
        self.priority = priority
        self.consider_worker_production = consider_worker_production
        self.waiting_reason: Optional[str] = None
        self._last_build_order = None

        super().__init__()

    @property
    def townhall_type(self) -> UnitTypeId:
        return get_new_townhall_type(self.knowledge.my_race)

    @property
    def unit_type(self) -> UnitTypeId:
        return self.townhall_type

    @property
    def current_active_base_count(self) -> int:
        # Keep the absolute target independent of remaining minerals and base
        # upgrades. New unfinished bases are counted by pending_build instead.
        upgraded = {UnitTypeId.LAIR, UnitTypeId.HIVE,
                    UnitTypeId.ORBITALCOMMAND, UnitTypeId.ORBITALCOMMANDFLYING,
                    UnitTypeId.PLANETARYFORTRESS}
        upgrades = {AbilityId.UPGRADETOLAIR_LAIR, AbilityId.UPGRADETOHIVE_HIVE,
                    AbilityId.UPGRADETOORBITAL_ORBITALCOMMAND,
                    AbilityId.UPGRADETOPLANETARYFORTRESS_PLANETARYFORTRESS}
        return sum(
            hall.is_ready or hall.type_id in upgraded
            or any(order.ability.id in upgrades for order in hall.orders)
            for hall in self.ai.townhalls
        )

    async def start(self, knowledge: "Knowledge"):
        await super().start(knowledge)
        self.gather_manager = knowledge.get_required_manager(IGatherPointSolver)
        self.zone_manager = knowledge.get_required_manager(ZoneManager)
        self.income_calculator = knowledge.get_required_manager(IIncomeCalculator)
        self.roles = knowledge.get_required_manager(UnitRoleManager)

    def _expansion_target(self):
        expand_here: Optional["Zone"] = None
        zone_currently_expanding: Optional["Zone"] = None
        expand_now = False
        zones = self.zone_manager.expansion_zones

        if self.priority_base_index is not None:
            zones = sorted(zones, key=lambda z: z.zone_index == self.priority_base_index, reverse=True)

        for zone in zones:  # type: "Zone"
            expanding = self.expanding_in(zone)
            if expand_here is None and zone.should_expand_here:
                if not expanding:
                    expand_here = zone
                    expand_now = zone.safe_expand_here
            if expanding:
                zone_currently_expanding = zone
        return expand_here, zone_currently_expanding, expand_now

    def execution_blocker(self) -> Optional[str]:
        """Expose the same site gate used by execute, without issuing orders."""
        active_bases = self.current_active_base_count
        if active_bases >= self.to_count:
            return None
        if self.pending_build(self.townhall_type) >= self.to_count - active_bases:
            return None
        zone, _, safe = self._expansion_target()
        if zone is None:
            return "no_available_expansion"
        if not safe:
            return "expansion_unsafe"
        return None

    async def execute(self) -> bool:
        self.waiting_reason = None
        if self._observe_build_rejection():
            return False
        expand_here, zone_currently_expanding, expand_now = self._expansion_target()
        active_bases = self.current_active_base_count

        if active_bases >= self.to_count:
            # We have expanded enough
            self.clear_worker()
            return True

        if expand_here is None or not self.ai.workers.exists:
            # Cannot expand
            if expand_here is None:
                self.waiting_reason = "no_available_expansion"
            self.clear_worker()
            return False

        if not expand_now:
            self.waiting_reason = "expansion_unsafe"

        worker = self.get_worker_builder(expand_here.center_location, self.builder_tag)
        pending_count = self.pending_build(self.townhall_type)

        if worker is None:
            return False

        # Inform our logic that we're looking to expand
        if zone_currently_expanding:
            self.gather_manager.set_expanding_to(zone_currently_expanding.center_location)
        else:
            self.gather_manager.set_expanding_to(expand_here.center_location)

        if pending_count:
            if self.has_build_order(worker):
                self.set_worker(worker)
            else:
                self.clear_worker()

            if active_bases + pending_count >= self.to_count:
                return True

        if expand_now:
            if self.ai.can_afford(self.townhall_type):
                if await self.build_expansion(expand_here):
                    return False
            else:
                self.possibly_move_worker(expand_here)

            cost = self.ai.calculate_cost(self.townhall_type)
            self.knowledge.reserve(cost.minerals, cost.vespene)

        return False

    def possibly_move_worker(self, zone: Zone):
        if not self.priority:
            return
        position = zone.center_location
        worker = self.get_worker_builder(position, self.builder_tag)
        if worker is None:
            return

        d = worker.distance_to(position)
        time = d / to_new_ticks(worker.movement_speed)
        available_minerals = self.ai.minerals - self.knowledge.reserved_minerals

        unit = self.ai._game_data.units[self.townhall_type.value]
        cost = self.ai._game_data.calculate_ability_cost(unit.creation_ability)

        if self.income_calculator.mineral_income > 0 and self.consider_worker_production:
            for town_hall in self.ai.townhalls:  # type: Unit
                # TODO: Zerg(?)
                if town_hall.orders:
                    starting_next_worker_in = -50 / self.income_calculator.mineral_income
                    for order in town_hall.orders:  # type: UnitOrder
                        if order.ability.id in train_worker_abilitites:
                            starting_next_worker_in += 12 * (1 - order.progress)

                    if starting_next_worker_in < time:
                        available_minerals -= 50  # should start producing workers soon now
                else:
                    available_minerals -= 50  # should start producing workers soon now

        if available_minerals + time * self.income_calculator.mineral_income >= cost.minerals:
            # Go wait
            self.set_worker(worker)

            worker.move(position)

    def set_worker(self, worker: Optional[Unit]) -> bool:
        if worker:
            self.roles.set_task(UnitTask.Building, worker)
            self.builder_tag = worker.tag
            return True

        self.builder_tag = None
        return False

    def clear_worker(self):
        if self.builder_tag is not None:
            self.roles.clear_task(self.builder_tag)
            self.builder_tag = None

    async def build_expansion(self, expand_here: "Zone") -> bool:
        worker = self.get_worker_builder(expand_here.center_location, self.builder_tag)

        if worker is not None:
            position = await self.checked_position(expand_here.center_location, max_distance=4)
            if position is None:
                return False
            self.print(f"Expanding to {position}")
            self.set_worker(worker)
            self._remember_build(worker, position)
            worker.build(self.townhall_type, position)
            return True
        return False

    async def debug_actions(self):
        if self.builder_tag is not None:
            worker: Unit = self.cache.by_tag(self.builder_tag)

            if worker and worker.orders:
                moving_status = ""
                for order in worker.orders:
                    if moving_status != "":
                        moving_status += ", "
                    moving_status += order.ability.id.name
                self.client.debug_text_world(moving_status, worker.position3d)

    def expanding_in(self, zone: "Zone") -> bool:
        """Are we already expanding to this location?"""
        creation_ability: AbilityId = self.ai._game_data.units[self.townhall_type.value].creation_ability
        for worker in self.ai.workers:
            for order in worker.orders:
                if order.ability.id == creation_ability.id:
                    if (hasattr(order.target, "x") and hasattr(order.target, "y")
                            and (order.target.x - zone.center_location.x) ** 2
                            + (order.target.y - zone.center_location.y) ** 2 <= 16):
                        return True

        return False
