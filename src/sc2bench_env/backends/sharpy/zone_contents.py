"""Compact, fog-safe contents for the platform Zone observation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional


def empty_contents() -> Dict[str, Dict[str, int]]:
    """Return the stable public shape used by every Zone row."""
    return {"units": {}, "buildings": {}}


def normalized_unit_name(unit: Any, adapter: Any) -> str:
    """Use the race adapter when possible, with a cross-race display fallback."""
    type_id = getattr(unit, "type_id", None)
    raw = str(getattr(type_id, "name", "") or "")
    normalize = getattr(adapter, "normalize_unit_name", None)
    name = normalize(raw) if callable(normalize) else None
    if name:
        return str(name)

    # python-sc2's Unit.name is game-data-backed and contains word boundaries
    # for enemy-race entities (for example "Photon Cannon").  Keep the raw ID
    # fallback for lightweight tests and unknown future units.
    display = str(getattr(unit, "name", "") or raw)
    display = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", display)
    return re.sub(r"[^a-zA-Z0-9]+", "_", display).strip("_").lower() or "unknown"


def is_currently_visible_enemy(unit: Any) -> bool:
    """True only for current evidence, never Sharpy fog memory or snapshots."""
    return (
        bool(getattr(unit, "is_visible", False))
        and not bool(getattr(unit, "is_memory", False))
        and not bool(getattr(unit, "is_snapshot", False))
        and not bool(getattr(unit, "is_hallucination", False))
        and float(getattr(unit, "health", 1) or 0) > 0
    )


def summarize_entities(
    entities: Iterable[Any],
    adapter: Any,
    *,
    include_loaded_passengers: bool = False,
) -> Dict[str, Dict[str, int]]:
    """Count entity types once and separate mobile units from structures."""
    result = empty_contents()
    seen_tags = set()

    def add(entity: Any) -> None:
        tag = getattr(entity, "tag", None)
        if tag is not None:
            tag = int(tag)
            if tag in seen_tags:
                return
            seen_tags.add(tag)
        group = "buildings" if bool(getattr(entity, "is_structure", False)) else "units"
        name = normalized_unit_name(entity, adapter)
        result[group][name] = result[group].get(name, 0) + 1

    source = list(entities or [])
    for entity in source:
        add(entity)
    if include_loaded_passengers:
        for transport in source:
            for passenger in list(getattr(transport, "passengers", None) or []):
                add(passenger)

    result["units"] = dict(sorted(result["units"].items()))
    result["buildings"] = dict(sorted(result["buildings"].items()))
    return result


@dataclass
class _EnemySighting:
    tag: int
    zone_id: str
    name: str
    is_structure: bool
    position: Any
    seen_at: float


@dataclass
class ZoneEnemyTracker:
    """Episode-local last sightings, independent of Sharpy's memory units.

    A record is invalidated when its last known position is currently visible
    and the same tag is absent.  This means the history describes the last
    observed position, not a claim that the enemy still exists elsewhere.
    """

    _records: Dict[int, _EnemySighting] = field(default_factory=dict)

    def reset(self) -> None:
        self._records.clear()

    def observe(
        self,
        *,
        ai: Any,
        adapter: Any,
        visible_by_zone: Mapping[str, Iterable[Any]],
        now: Optional[float] = None,
    ) -> Dict[str, Dict[str, Any]]:
        timestamp = float(getattr(ai, "time", 0.0) if now is None else now)
        current_by_zone: Dict[str, list[Any]] = {
            str(zone_id): [] for zone_id in visible_by_zone
        }
        current_tags = set()

        for zone_id, entities in visible_by_zone.items():
            for entity in entities or []:
                if not is_currently_visible_enemy(entity):
                    continue
                raw_tag = getattr(entity, "tag", None)
                tag = int(raw_tag) if raw_tag is not None else id(entity)
                if tag in current_tags:
                    continue
                current_tags.add(tag)
                current_by_zone[str(zone_id)].append(entity)
                self._records[tag] = _EnemySighting(
                    tag=tag,
                    zone_id=str(zone_id),
                    name=normalized_unit_name(entity, adapter),
                    is_structure=bool(getattr(entity, "is_structure", False)),
                    position=getattr(entity, "position", entity),
                    seen_at=timestamp,
                )

        is_visible = getattr(ai, "is_visible", None)
        if callable(is_visible):
            for tag, record in list(self._records.items()):
                if tag in current_tags:
                    continue
                try:
                    position_visible = bool(is_visible(record.position))
                except Exception:
                    position_visible = False
                if position_visible:
                    del self._records[tag]

        output: Dict[str, Dict[str, Any]] = {}
        for zone_id, entities in current_by_zone.items():
            output[zone_id] = {
                "visible_enemy_contents": summarize_entities(entities, adapter),
                "last_seen_enemy_contents": empty_contents(),
                "enemy_information_age_seconds": None,
            }

        ages: Dict[str, list[float]] = {zone_id: [] for zone_id in current_by_zone}
        for tag, record in self._records.items():
            if tag in current_tags or record.zone_id not in output:
                continue
            group = "buildings" if record.is_structure else "units"
            history = output[record.zone_id]["last_seen_enemy_contents"][group]
            history[record.name] = history.get(record.name, 0) + 1
            ages[record.zone_id].append(max(0.0, timestamp - record.seen_at))

        for zone_id, values in output.items():
            for group in ("units", "buildings"):
                history = values["last_seen_enemy_contents"][group]
                values["last_seen_enemy_contents"][group] = dict(sorted(history.items()))
            if ages[zone_id]:
                # One scalar keeps the row compact.  The oldest represented
                # sighting is the conservative freshness signal for the group.
                values["enemy_information_age_seconds"] = round(max(ages[zone_id]), 1)
        return output
