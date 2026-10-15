"""Explicit input compatibility; the catalog/schema remain canonical-only."""
from __future__ import annotations


from types import MappingProxyType
from typing import Any, Mapping

from sc2bench_env.interface.races import require_supported_own_race


# Add only reviewed, unambiguous aliases. Never guess from spelling similarity.
TARGET_ALIASES = MappingProxyType({
    "research": MappingProxyType({"stim_pack": "stimpack"}),
})


RACE_TARGET_ALIASES = MappingProxyType({
    "terran": TARGET_ALIASES,
    # ``warpgate`` remains the canonical build target.  Only research uses
    # the underscored catalog name, so this compatibility must stay verb-local.
    "protoss": MappingProxyType({
        "research": MappingProxyType({"warpgate": "warp_gate"}),
    }),
})


def normalize_target_aliases(
    raw: Mapping[str, Any], *, index: int = 0, race: str = "terran",
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Copy an entry and resolve registered aliases without changing its fields."""
    require_supported_own_race(race)
    aliases = RACE_TARGET_ALIASES.get(race, {})
    entry = dict(raw)
    action = entry.get("action")
    if action == "scout" and entry.get("route") == ["all"]:
        entry["route"] = "all"
        return entry, ({
            "entry_index": index,
            "name": "scout",
            "field": "route",
            "original": ["all"],
            "canonical": "all",
        },)

    # Public army tools have one clear responsibility each.  Runtime and
    # Sharpy continue to consume the established combat/retreat tasks.
    if action == "dispatch_group":
        entry["action"] = "combat"
        entry["style"] = entry.pop("order", None)
        return entry, ()
    if action in {"attack_group", "defend_group"}:
        entry["action"] = "combat"
        entry["style"] = "attack" if action == "attack_group" else "defend"
        return entry, ()
    if action == "reinforce_group":
        entry["action"] = "combat"
        return entry, ()
    if action == "retreat_group":
        entry["action"] = "retreat"
        return entry, ()

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
