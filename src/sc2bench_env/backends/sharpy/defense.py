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
