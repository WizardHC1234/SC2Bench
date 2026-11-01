"""Completed upgraded forms that retain an earlier building's technology.

These are prerequisite/producer equivalents, not interchangeable morph sources.
"""
from sc2bench_env.interface.races import require_supported_own_race


_EQUIVALENTS = {
    "terran": {"command_center": ("command_center", "orbital_command", "planetary_fortress")},
    "protoss": {"gateway": ("gateway", "warpgate")},
    "zerg": {
        "hatchery": ("hatchery", "lair", "hive"),
        "lair": ("lair", "hive"),
        "spire": ("spire", "greater_spire"),
    },
}


def prerequisite_keys(name: str, *, race: str) -> tuple[str, ...]:
    require_supported_own_race(race)
    return _EQUIVALENTS[race].get(name, (name,))
