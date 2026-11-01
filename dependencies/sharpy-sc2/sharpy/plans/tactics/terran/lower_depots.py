from sc2.ids.ability_id import AbilityId
from sharpy.plans.acts import ActBase
from sc2.ids.unit_typeid import UnitTypeId
from sc2.unit import Unit


class LowerDepots(ActBase):
    async def execute(self) -> bool:
        depot: Unit
        for depot in self.cache.own(UnitTypeId.SUPPLYDEPOT):
            if not depot.is_ready or depot.orders:
                continue
            if not self.ai.enemy_units.not_flying.closer_than(5, depot.position).exists:
                # lower depot
                depot(AbilityId.MORPH_SUPPLYDEPOT_LOWER)

        for depot in self.cache.own(UnitTypeId.SUPPLYDEPOTLOWERED):
            if not depot.is_ready or depot.orders:
                continue
            if self.ai.enemy_units.not_flying.closer_than(5, depot.position).exists:
                # A lowered Depot has a 2x2 footprint. Ground units may walk
                # across it; raising while they overlap is rejected by SC2.
                occupied = False
                for unit in (*getattr(self.ai, 'units', ()), *self.ai.enemy_units):
                    if unit.is_flying or getattr(unit, 'is_burrowed', False):
                        continue
                    dx = max(0, abs(unit.position.x - depot.position.x) - 1)
                    dy = max(0, abs(unit.position.y - depot.position.y) - 1)
                    if dx * dx + dy * dy < (float(unit.radius) + .05) ** 2:
                        occupied = True
                        break
                if not occupied:
                    depot(AbilityId.MORPH_SUPPLYDEPOT_RAISE)

        return True
