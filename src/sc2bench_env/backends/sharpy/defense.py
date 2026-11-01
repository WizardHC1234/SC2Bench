"""Auto zone defense that never pulls model combat-bound units."""
from __future__ import annotations


from typing import List, Optional

from sharpy.plans.tactics.zone_defense import PlanZoneDefense


class PlanZoneDefenseSafe(PlanZoneDefense):
    """Same as Sharpy PlanZoneDefense, but skips ``ai.bench_combat_tags``.

    Combat missions mark bound units as Reserved and register tags on
    ``ai.bench_combat_tags``. Local scout hunters previously could still yank
    Attacking / Fighting units; filter those tags out here.
    """

    async def execute(self):
        from sc2.ids.unit_typeid import UnitTypeId
        from sharpy.managers.core.roles import UnitTask
        from sc2bench_env.backends.sharpy.combat_styles import visible_combat_enemies

        pending = {cast['caster']: cast for task in getattr(self.ai, '_macro_tasks', ())
                   if task.get('action') in {'inject_larva', 'spawn_creep_tumor'}
                   for cast in [getattr(task.get('_act'), '_pending_cast', None)] if cast}
        if not pending:
            return await super().execute()
        held = []
        for unit in self.ai.units:
            if unit.type_id != UnitTypeId.QUEEN or unit.tag not in pending:
                continue
            role = self.roles.unit_role(unit)
            if role not in {UnitTask.Idle, UnitTask.Moving}:
                continue
            nearby = [ally for ally in self.ai.units if ally.tag != unit.tag and unit.distance_to(ally) <= 7]
            contact = visible_combat_enemies(self.ai, unit, nearby, unit_values=self.unit_values)
            threatened = any(enemy.is_visible and not enemy.is_memory and not enemy.is_snapshot
                and self.unit_values.real_range(enemy, unit) >= unit.distance_to(enemy)
                for enemy in self.ai.all_enemy_units)
            if not contact and not threatened:
                self.roles.set_task(UnitTask.Reserved, unit)
                held.append((unit, role))
        try:
            return await super().execute()
        finally:
            for unit, role in held:
                if self.roles.unit_role(unit) == UnitTask.Reserved:
                    self.roles.set_task(role, unit)

    def _mission_reserved_tags(self) -> set:
        return (set(getattr(self.ai, "bench_combat_tags", None) or ())
                | set(getattr(self.ai, "bench_group0_tags", None) or ())
                | set(getattr(self.ai, "bench_bunker_tags", None) or ()))

    def _pick_local_scout_hunters(
        self,
        zone,
        scout_position,
        prefer_tags: Optional[List[int]] = None,
    ):
        hunters = super()._pick_local_scout_hunters(zone, scout_position, prefer_tags)
        reserved = self._mission_reserved_tags()
        if not reserved or not hunters.exists:
            return hunters
        kept = [unit for unit in hunters if unit.tag not in reserved]
        from sc2.units import Units

        return Units(kept, self.ai)
