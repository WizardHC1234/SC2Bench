from math import ceil
from typing import TYPE_CHECKING
from sharpy.plans.acts import ActBase
from sharpy.managers.core.roles import UnitTask
from sharpy.general.zone import Zone
from sc2.constants import UnitTypeId
from sc2.unit import Unit

if TYPE_CHECKING:
    from sharpy.managers.core import UnitRoleManager


class Repair(ActBase):
    def __init__(self):
        super().__init__()

    async def execute(self) -> bool:
        roles: "UnitRoleManager" = self.roles
        current_repairers = []
        for zone in self.zone_manager.our_zones:
            pre_repairer = 0
            balance = zone.our_power.power - zone.assaulting_enemy_power.power
            if balance < -5:
                if balance < -10:
                    pre_repairer = 6
                else:
                    pre_repairer = 3

            repairing_zone_count = 0

            for worker in zone.our_workers:  # type: Unit
                if not worker.orders:
                    continue
                if worker.is_repairing:
                    current_repairers.append(worker.tag)
                    roles.set_task(UnitTask.Building, worker)
                    repairing_zone_count += 1

            for unit in zone.our_units:
                if self.should_repair(unit) and self.can_fund_repair(unit):
                    desired_count = self.solve_scv_count(zone, unit)

                    if repairing_zone_count < desired_count:
                        for worker in zone.our_workers:  # type: Unit
                            if (self.available_worker(worker) and worker.tag != unit.tag and not worker.is_repairing
                                    and worker.tag not in current_repairers):
                                worker.repair(unit)
                                current_repairers.append(worker.tag)
                                roles.set_task(UnitTask.Building, worker)
                                repairing_zone_count += 1
                                if repairing_zone_count >= desired_count:
                                    break

            if repairing_zone_count < pre_repairer:
                to_repair = zone.our_units.filter(lambda u: u.is_ready and (u.is_structure or u.is_mechanical)
                                                 and u.health < u.health_max and self.can_fund_repair(u))
                enemies = zone.known_enemy_units
                if to_repair and enemies:
                    for worker in zone.our_workers:  # type: Unit
                        if self.available_worker(worker) and not worker.is_repairing and worker.tag not in current_repairers:
                            closest = to_repair.closest_to(enemies.center)
                            if worker.tag == closest.tag:
                                continue
                            worker.repair(closest)
                            current_repairers.append(worker.tag)
                            roles.set_task(UnitTask.Building, worker)
                            repairing_zone_count += 1
                            if repairing_zone_count >= pre_repairer:
                                break

        return True

    def available_worker(self, worker):
        return (self.roles.unit_role(worker) in {UnitTask.Idle, UnitTask.Gathering}
                and not worker.is_constructing_scv
                and worker.tag not in self.ai.unit_tags_received_action)

    def can_fund_repair(self, unit):
        cost = self.ai.calculate_unit_value(unit.type_id)
        return ((cost.minerals <= 0 or self.ai.minerals > 0)
                and (cost.vespene <= 0 or self.ai.vespene > 0))

    def should_repair(self, unit: Unit) -> bool:
        if not unit.is_ready:
            return False
        if unit.health_percentage < 0.95:
            if unit.type_id == UnitTypeId.BUNKER:
                return True
            elif (
                unit.type_id == UnitTypeId.COMMANDCENTER
                or unit.type_id == UnitTypeId.ORBITALCOMMAND
                or unit.type_id == UnitTypeId.PLANETARYFORTRESS
            ):
                return True
        if unit.health_percentage < 0.3 and unit.is_structure:
            return True
        if unit.health_percentage < 0.75 and unit.is_mechanical:
            return True
        return False

    def solve_scv_count(self, zone: Zone, unit: Unit) -> int:
        power_max = max(1, zone.known_enemy_power.power / 3)
        if unit.type_id == UnitTypeId.BUNKER:
            hp_max = 6
        elif (
            unit.type_id == UnitTypeId.COMMANDCENTER
            or unit.type_id == UnitTypeId.ORBITALCOMMAND
            or unit.type_id == UnitTypeId.PLANETARYFORTRESS
        ):
            hp_max = 12
        elif unit.is_structure:
            hp_max = 1
        else:
            hp_max = 2
        return ceil(min(power_max, hp_max))
