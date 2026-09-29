"""Mass Recall for one Protoss retreat. Only the current main Nexus is eligible."""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from sc2.ids.ability_id import AbilityId
from sc2.position import Point2

# Snapshots do not store energy or radius. Both 4.10 and 5.0.16 use this retail Nexus recall.
RECALL_ENERGY = 50.0
RECALL_RADIUS = 2.5
MAIN_NEXUS_RADIUS = 20.0
RECALL_ABILITY = AbilityId.EFFECT_MASSRECALL_NEXUS


def recall_energy_cost(game_data=None) -> float:
    """Prefer the live client cost. Fall back to the shared retail cost."""
    if game_data is not None:
        try:
            cost = game_data.calculate_ability_cost(RECALL_ABILITY)
            energy = float(getattr(cost, "energy", 0) or 0)
            if energy > 0:
                return energy
        except Exception:
            pass
    return RECALL_ENERGY


def select_main_nexus(nexuses: Sequence, start) -> Tuple[Optional[object], Optional[str]]:
    """The Nexus at the starting base. A living Nexus elsewhere is not a substitute."""
    near = [nexus for nexus in nexuses if nexus.distance_to(start) <= MAIN_NEXUS_RADIUS]
    if not near:
        return None, "nexus_missing"
    ready = [
        nexus for nexus in near
        if getattr(nexus, "is_ready", True) and float(getattr(nexus, "health", 1) or 0) > 0
    ]
    if not ready:
        return None, "nexus_incomplete"
    return min(ready, key=lambda nexus: nexus.distance_to(start)), None


def group_center(members: Sequence) -> Optional[Point2]:
    if not members:
        return None
    return Point2((
        sum(unit.position.x for unit in members) / len(members),
        sum(unit.position.y for unit in members) / len(members),
    ))


def split_by_radius(members: Sequence, center, radius: float = RECALL_RADIUS):
    inside, outside = [], []
    for unit in members:
        (inside if unit.distance_to(center) <= radius else outside).append(unit)
    return inside, outside
