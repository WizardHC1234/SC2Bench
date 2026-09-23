"""Explicit input compatibility; the catalog/schema remain canonical-only."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

from sc2bench_env.interface.races import require_supported_own_race


# Add only reviewed, unambiguous aliases. Never guess from spelling similarity.
TARGET_ALIASES = MappingProxyType({
    "research": MappingProxyType({"stim_pack": "stimpack"}),
})


RACE_TARGET_ALIASES = MappingProxyType({"terran": TARGET_ALIASES})


def normalize_target_aliases(
    raw: Mapping[str, Any], *, index: int = 0, race: str = "terran",
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Copy an entry and resolve registered aliases without changing its fields."""
    require_supported_own_race(race)
    aliases = RACE_TARGET_ALIASES.get(race, {})
    entry = dict(raw)
    action = entry.get("action")
    target = entry.get("target")
    if not isinstance(action, str) or not isinstance(target, str):
        return entry, ()
    action = action.strip().lower()
    verb = action
    if action == "cancel":
        target_action = entry.get("target_action")
        if not isinstance(target_action, str):
            return entry, ()
        verb = target_action.strip().lower()
    canonical = aliases.get(verb, {}).get(target.strip().lower())
    if canonical is None:
        return entry, ()
    entry["target"] = canonical
    change = {"entry_index": index, "name": action, "field": "target",
              "original": target, "canonical": canonical}
    if action == "cancel":
        change["target_action"] = verb
    return entry, (change,)
