"""Stable display order by economy, technology and production families."""
from __future__ import annotations

from typing import Iterable

from sc2bench_env.catalog.races import get_catalog
from sc2bench_env.interface.races import require_supported_own_race


_BUILD_ORDER = {
    "terran": (
        "command_center", "refinery", "supply_depot",
        "barracks", "barracks_techlab", "barracks_reactor",
        "factory", "factory_techlab", "factory_reactor",
        "starport", "starport_techlab", "starport_reactor",
        "engineering_bay", "armory", "ghost_academy", "fusion_core",
        "bunker", "missile_turret", "sensor_tower",
    ),
    "protoss": (
        "nexus", "assimilator", "pylon",
        "gateway", "warpgate", "cybernetics_core", "forge",
        "twilight_council", "templar_archives", "dark_shrine",
        "robotics_facility", "robotics_bay", "stargate", "fleet_beacon",
        "photon_cannon", "shield_battery",
    ),
    "zerg": (
        "hatchery", "extractor", "spawning_pool", "baneling_nest", "roach_warren",
        "evolution_chamber", "hydralisk_den", "lurker_den", "infestation_pit",
        "spire", "greater_spire", "ultralisk_cavern", "nydus_network",
        "spine_crawler", "spore_crawler",
    ),
}

_TRAIN_ORDER = {
    "terran": (
        "scv", "marine", "reaper", "marauder", "ghost",
        "hellion", "hellbat", "widow_mine", "cyclone", "siege_tank", "thor",
        "viking", "medivac", "liberator", "raven", "banshee", "battlecruiser",
    ),
    "protoss": (
        "probe", "zealot", "stalker", "sentry", "adept",
        "high_templar", "dark_templar", "archon",
        "observer", "warp_prism", "immortal", "colossus", "disruptor",
        "phoenix", "oracle", "void_ray", "tempest", "carrier", "mothership",
    ),
    "zerg": (
        "drone", "overlord", "overseer", "transport_overlord", "queen",
        "zergling", "baneling", "roach", "ravager", "hydralisk", "lurker",
        "infestor", "swarm_host", "ultralisk",
        "mutalisk", "corruptor", "brood_lord", "viper",
    ),
}

_MORPH_ORDER = {
    "terran": ("orbital_command", "planetary_fortress"),
    "protoss": (),
    "zerg": ("lair", "hive"),
}


def _research_family(name: str) -> tuple[str, int]:
    prefix, _, suffix = name.rpartition("_")
    return (prefix, int(suffix)) if suffix.isdigit() else (name, 0)


def ordered_target_names(names: Iterable[str], *, race: str, action: str) -> list[str]:
    """Order existing names for presentation; never filter or schedule work."""
    require_supported_own_race(race)
    if action == "research":
        buildings = {name: index for index, name in enumerate(_BUILD_ORDER[race])}
        specs = {spec.name: spec for spec in get_catalog(race=race).targets
                 if spec.action == "research"}
        families = {}
        for spec in specs.values():
            family, _ = _research_family(spec.name)
            families.setdefault((spec.produced_at, family), len(families))

        def research_key(name):
            spec = specs.get(name)
            if spec is None:
                return (len(buildings), len(families), 0, name)
            family, level = _research_family(name)
            return (buildings.get(spec.produced_at, len(buildings)),
                    families[(spec.produced_at, family)], level, name)

        return sorted(names, key=research_key)

    order = {"build": _BUILD_ORDER, "train": _TRAIN_ORDER,
             "morph_townhall": _MORPH_ORDER}[action][race]
    ranks = {name: index for index, name in enumerate(order)}
    return sorted(names, key=lambda name: (ranks.get(name, len(ranks)), name))
