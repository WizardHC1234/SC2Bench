"""Stable zone_id registry keyed by expansion center coordinates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sc2bench_env.backends.sharpy.zone_contents import ZoneEnemyTracker
from sc2bench_env.backends.sharpy.zone_resources import ZoneResourceTracker


def _center_key(x: float, y: float) -> Tuple[float, float]:
    return (round(float(x), 1), round(float(y), 1))


@dataclass
class ZoneRegistry:
    """Maps stable platform zone_id <-> expansion center.

    zone_id values are assigned on first sight of a center and never reused for a
    different center within an episode. This avoids treating Sharpy's current
    expansion_zones list index as a long-lived identifier.
    """

    zone_ids: List[str] = field(default_factory=list)
    _center_keys: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    _key_to_id: Dict[Tuple[float, float], str] = field(default_factory=dict)
    _roles: Dict[Tuple[float, float], str] = field(default_factory=dict)
    resources: ZoneResourceTracker = field(default_factory=ZoneResourceTracker)
    enemies: ZoneEnemyTracker = field(default_factory=ZoneEnemyTracker)
    _topology_paths: Dict[Tuple[str, str], Any] = field(default_factory=dict)
    _topology_links: Dict[str, Any] = field(default_factory=dict)
    _topology_signature: Any = None

    def reset(self) -> None:
        self.zone_ids.clear()
        self._center_keys.clear()
        self._key_to_id.clear()
        self._roles.clear()
        self.resources.reset()
        self.enemies.reset()
        self._topology_paths.clear()
        self._topology_links.clear()
        self._topology_signature = None

    def sync_roles(self, ai: Any, zone_manager: Any, zones: List[Any]) -> None:
        """Assign topology roles by coordinates, never by mutable list index.

        Enemy roles are withheld on multi-spawn maps until Sharpy confirms the
        enemy start; this avoids leaking or freezing a guessed starting point.
        Once a special role is known, it remains fixed for the episode.
        """
        centers = [zone.center_location for zone in zones]

        def closest(point: Any) -> Optional[Tuple[float, float]]:
            if point is None or not centers:
                return None
            center = min(centers, key=lambda item: item.distance_to(point))
            return _center_key(center.x, center.y)

        def assign(point: Any, role: str) -> None:
            key = closest(point)
            if key is not None and key not in self._roles:
                self._roles[key] = role

        assign(getattr(ai, "start_location", None), "own_main")
        own_natural = getattr(zone_manager, "own_natural", None)
        assign(getattr(own_natural, "center_location", None), "own_natural")

        starts = list(getattr(ai, "enemy_start_locations", None) or [])
        try:
            enemy_confirmed = bool(getattr(zone_manager, "enemy_start_location_found", False))
        except Exception:
            enemy_confirmed = False
        if len(starts) == 1 or enemy_confirmed:
            enemy_location = getattr(zone_manager, "found_enemy_start", None)
            if enemy_location is None and len(starts) == 1:
                enemy_location = starts[0]
            if enemy_location is None:
                try:
                    enemy_location = getattr(zone_manager, "enemy_start_location", None)
                except Exception:
                    enemy_location = None
            assign(enemy_location, "enemy_main")
            enemy_natural = getattr(zone_manager, "enemy_natural", None)
            assign(getattr(enemy_natural, "center_location", None), "enemy_natural")

    def role_for_center(self, x: float, y: float) -> str:
        return self._roles.get(_center_key(x, y), "other_expansion")

    def sync_from_centers(self, centers: List[Tuple[float, float]]) -> List[str]:
        """Ensure every center has a stable id; return ids in input order."""
        ordered: List[str] = []
        for x, y in centers:
            key = _center_key(x, y)
            zone_id = self._key_to_id.get(key)
            if zone_id is None:
                index = 0
                used = set(self.zone_ids)
                while True:
                    candidate = f"zone_{index}"
                    if candidate not in used:
                        break
                    index += 1
                zone_id = candidate
                self._key_to_id[key] = zone_id
                self._center_keys[zone_id] = key
                self.zone_ids.append(zone_id)
            ordered.append(zone_id)
        return ordered

    def center_for(self, zone_id: str) -> Optional[Tuple[float, float]]:
        return self._center_keys.get(zone_id)

    def resolve_zone(self, zone_manager: Any, zone_id: str) -> Any:
        """Return the current Sharpy Zone matching a stable id, if present."""
        key = self._center_keys.get(zone_id)
        if key is None or zone_manager is None:
            return None
        for zone in list(getattr(zone_manager, "expansion_zones", None) or []):
            center = getattr(zone, "center_location", None)
            if center is None:
                continue
            if _center_key(center.x, center.y) == key:
                return zone
        return None
