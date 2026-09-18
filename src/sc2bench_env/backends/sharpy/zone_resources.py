"""Episode-local, position-keyed resource facts without fog-of-war guesses."""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


def _position(unit) -> Tuple[float, float]:
    return (round(float(unit.position.x), 1), round(float(unit.position.y), 1))


def _current(unit) -> bool:
    return (getattr(unit, "is_visible", False)
            and not getattr(unit, "is_memory", False)
            and not getattr(unit, "is_snapshot", False))


def _quantity(unit, field) -> Optional[int]:
    value = getattr(unit, field, None)
    return None if value is None else max(0, int(value))


@dataclass
class _Node:
    position: Any
    kind: str
    initial: Optional[int] = None
    remaining: Optional[int] = None
    occupied: Optional[bool] = None
    seen_at: Optional[float] = None


@dataclass
class ZoneResourceTracker:
    """Keep initial quantities fixed; only update amounts from current vision.

    The expansion resource groups supply public node positions, not quantities.
    If an amount is unavailable at game time zero, initial stays unknown: a
    later scout's depleted quantity must not become the map's initial amount.
    Gas is keyed by position because building a refinery changes entity tags.
    """
    _zones: Dict[Tuple[float, float], Dict[Tuple[float, float], _Node]] = field(default_factory=dict)

    def reset(self) -> None:
        self._zones.clear()

    def read_zone(self, ai, zone) -> dict:
        center = zone.center_location
        key = (round(float(center.x), 1), round(float(center.y), 1))
        groups = getattr(ai, "expansion_locations_dict", {})
        layout = groups.get(center) if groups else None
        if layout is None:
            layout = getattr(zone, "_original_mineral_fields", None)
        if layout is None:
            return {"minerals_remaining": None, "minerals_initial": None,
                    "vespene_remaining": None, "vespene_initial": None,
                    "geyser_slots": None, "owned_gas_structure_count": 0,
                    "available_geyser_slots": None, "resource_visibility": "unknown"}

        nodes = self._zones.setdefault(key, {})
        now = float(getattr(ai, "time", 0))
        for resource in layout:
            kind = ("minerals" if getattr(resource, "is_mineral_field", False)
                    else "vespene" if getattr(resource, "is_vespene_geyser", False) else None)
            if kind is None or _position(resource) in nodes:
                continue
            node = _Node(position=resource.position, kind=kind)
            if now == 0 and _current(resource):
                amount_field = "mineral_contents" if kind == "minerals" else "vespene_contents"
                node.initial = _quantity(resource, amount_field)
            nodes[_position(resource)] = node

        neutral = list(getattr(ai, "mineral_field", []) or []) + list(
            getattr(ai, "vespene_geyser", []) or [])
        own_gas = list(getattr(ai, "gas_buildings", []) or [])
        enemy_gas = [u for u in getattr(ai, "enemy_structures", []) or []
                     if getattr(u, "is_vespene_geyser", False) and _current(u)]
        gas = own_gas + enemy_gas
        current_resources = {_position(u): u for u in neutral if _current(u)}
        # During construction a refinery can report zero without being depleted.
        current_resources.update({_position(u): u for u in gas
                                  if _current(u) and getattr(u, "is_ready", False)})
        gas_positions = {_position(u) for u in gas}
        own_positions = {_position(u) for u in own_gas}
        is_visible = getattr(ai, "is_visible", lambda position: False)
        for position, node in nodes.items():
            resource = current_resources.get(position)
            if resource is not None:
                amount_field = "mineral_contents" if node.kind == "minerals" else "vespene_contents"
                amount = _quantity(resource, amount_field)
                if amount is not None:
                    node.remaining = amount
                    node.seen_at = now
            elif node.kind == "minerals" and is_visible(node.position):
                # Disappearance in vision confirms exhaustion/removal, not fog.
                node.remaining = 0
                node.seen_at = now
            if node.kind == "vespene" and (is_visible(node.position) or position in own_positions):
                node.occupied = position in gas_positions

        minerals = [node for node in nodes.values() if node.kind == "minerals"]
        geysers = [node for node in nodes.values() if node.kind == "vespene"]

        def total(items, attribute):
            values = [getattr(item, attribute) for item in items]
            return None if any(value is None for value in values) else sum(values)

        if not nodes or all(node.seen_at == now for node in nodes.values()):
            visibility = "visible"
        elif all(node.seen_at is None for node in nodes.values()):
            visibility = "unknown"
        elif any(node.seen_at == now for node in nodes.values()):
            visibility = "partial"
        else:
            visibility = "last_seen"
        return {
            "minerals_remaining": total(minerals, "remaining"),
            "minerals_initial": total(minerals, "initial"),
            "vespene_remaining": total(geysers, "remaining"),
            "vespene_initial": total(geysers, "initial"),
            "geyser_slots": len(geysers),
            "owned_gas_structure_count": sum(position in own_positions
                                              for position, node in nodes.items() if node.kind == "vespene"),
            "available_geyser_slots": (None if any(node.occupied is None for node in geysers)
                                       else sum(not node.occupied for node in geysers)),
            "resource_visibility": visibility,
        }
