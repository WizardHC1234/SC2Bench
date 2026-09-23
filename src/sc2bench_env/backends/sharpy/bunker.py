"""Automatic bunker garrison. No agent load or unload action.

Only completed bunkers and free group_0 Marines are used. Loaded passengers
stay out of the home pool. This version does not unload; a rejected combat
order reports the garrison instead of cycling load and unload.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts import ActBase

BUNKER_CAPACITY = 4
UNTHREATENED_GARRISON_FRACTION = 0.5
BUNKER_THREAT_RADIUS = 12.0


def assign_bunker_marines(
    bunkers: Sequence[Mapping[str, Any]],
    free_marine_tags: Sequence[int],
    *,
    capacity: int = BUNKER_CAPACITY,
    unthreatened_fraction: float = UNTHREATENED_GARRISON_FRACTION,
) -> list[tuple[Any, int]]:
    """Choose unique Marines for bunker slots.

    Threatened bunkers fill first, up to capacity. Unthreatened bunkers share
    at most ``floor(available * fraction)`` of the free Marines.
    ``occupied`` already includes passengers and Marines walking in.
    """
    remaining = [int(tag) for tag in free_marine_tags]
    unthreatened_budget = int(len(remaining) * unthreatened_fraction)
    ordered = sorted(bunkers, key=lambda row: (not bool(row.get("threatened")), row.get("id")))
    assignments: list[tuple[Any, int]] = []
    used: set[int] = set()
    for bunker in ordered:
        slots = max(0, int(capacity) - int(bunker.get("occupied", 0)))
        if slots <= 0 or not remaining:
            continue
        take = slots if bunker.get("threatened") else min(slots, unthreatened_budget)
        chosen: list[int] = []
        for tag in remaining:
            if len(chosen) >= take:
                break
            if tag in used:
                continue
            chosen.append(tag)
            used.add(tag)
        if not bunker.get("threatened"):
            unthreatened_budget -= len(chosen)
        remaining = [tag for tag in remaining if tag not in used]
        assignments.extend((bunker.get("id"), tag) for tag in chosen)
    return assignments


class PlanBunkerDefense(ActBase):
    def __init__(self, adapter):
        super().__init__()
        self.adapter = adapter
        self._incoming: dict[int, int] = {}

    async def start(self, knowledge):
        await super().start(knowledge)
        self.ai.bench_bunker_tags = set()

    def _marine_passengers(self, bunker) -> list[int]:
        tags = []
        for passenger in getattr(bunker, "passengers", []) or []:
            type_name = str(getattr(getattr(passenger, "type_id", None), "name", "")).upper()
            if type_name == "MARINE" and getattr(passenger, "tag", None) is not None:
                tags.append(int(passenger.tag))
        return tags

    def _threatened(self, bunker) -> bool:
        for enemy in getattr(self.ai, "enemy_units", []) or []:
            if not getattr(enemy, "is_visible", False):
                continue
            if getattr(enemy, "is_memory", False) or getattr(enemy, "is_snapshot", False):
                continue
            try:
                if enemy.distance_to(bunker) <= BUNKER_THREAT_RADIUS:
                    return True
            except Exception:
                continue
        return False

    def _blocked_tags(self) -> set[int]:
        blocked = set(getattr(self.ai, "bench_combat_tags", set()) or ())
        roles = getattr(self, "roles", None)
        if roles is None:
            return blocked
        from sharpy.managers.core.roles import UnitTask

        for unit in self.ai.units:
            if roles.is_in_role(UnitTask.Scouting, unit):
                blocked.add(int(unit.tag))
        return blocked

    async def execute(self) -> bool:
        bunkers = [
            structure for structure in self.ai.structures(UnitTypeId.BUNKER)
            if structure.is_ready and float(getattr(structure, "health", 0) or 0) > 0
        ]
        marines = {
            int(unit.tag): unit
            for unit in self.ai.units(UnitTypeId.MARINE).ready
            if not unit.is_structure
        }
        passenger_tags: set[int] = set()
        rows = []
        for bunker in bunkers:
            loaded = self._marine_passengers(bunker)
            passenger_tags.update(loaded)
            rows.append({
                "id": int(bunker.tag),
                "occupied": len(loaded),
                "threatened": self._threatened(bunker),
                "unit": bunker,
            })
        live_bunkers = {row["id"]: row["unit"] for row in rows}
        self._incoming = {
            tag: bunker_tag
            for tag, bunker_tag in self._incoming.items()
            if bunker_tag in live_bunkers and tag in marines and tag not in passenger_tags
            and self._still_loading(marines[tag], live_bunkers[bunker_tag])
        }
        for row in rows:
            row["occupied"] += sum(1 for bunker_tag in self._incoming.values() if bunker_tag == row["id"])

        blocked = self._blocked_tags() | set(self._incoming) | passenger_tags
        home = getattr(self.ai, "bench_group0_tags", None)
        if not isinstance(home, set):
            home = set()
        free = sorted(
            tag for tag, unit in marines.items()
            if tag in home and tag not in blocked and not getattr(unit, "is_hallucination", False)
        )
        assignments = assign_bunker_marines(
            [{"id": row["id"], "occupied": row["occupied"], "threatened": row["threatened"]} for row in rows],
            free,
        )
        by_id = {row["id"]: row["unit"] for row in rows}
        for bunker_tag, marine_tag in assignments:
            marine = marines.get(marine_tag)
            bunker = by_id.get(bunker_tag)
            if marine is None or bunker is None:
                continue
            self._incoming[marine_tag] = bunker_tag
            marine(AbilityId.SMART, bunker)

        reserved = passenger_tags | set(self._incoming)
        self.ai.bench_bunker_tags = set(reserved)
        group0 = getattr(self.ai, "bench_group0_tags", None)
        if isinstance(group0, set):
            group0.difference_update(reserved)
        return True

    def _still_loading(self, marine, bunker) -> bool:
        """Keep a walking reservation. Drop it when the Marine is idle and still far away."""
        if not getattr(marine, "is_idle", False):
            return True
        try:
            return marine.distance_to(bunker) <= 6
        except Exception:
            return False
