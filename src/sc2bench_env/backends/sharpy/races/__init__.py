"""Single, lazy backend entry point for implemented race adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sc2bench_env.interface.races import require_supported_own_race

if TYPE_CHECKING:
    from sc2bench_env.backends.sharpy.races.base import RaceAdapter


def get_adapter(race: str) -> RaceAdapter:
    """Select an implemented adapter without importing optional races eagerly."""
    require_supported_own_race(race)
    # Keep the existing Terran factory/Act wiring and its test injection point.
    if race == "terran":
        from sc2bench_env.backends.sharpy.races.terran import get_adapter as get_terran_adapter
        return get_terran_adapter(race)
    if race == "protoss":
        from sc2bench_env.backends.sharpy.races.protoss import get_adapter as get_protoss_adapter
        return get_protoss_adapter(race)
    if race == "zerg":
        from sc2bench_env.backends.sharpy.races.zerg import get_adapter as get_zerg_adapter
        return get_zerg_adapter(race)
    raise ValueError(f"No race adapter implemented for own race {race!r}")
