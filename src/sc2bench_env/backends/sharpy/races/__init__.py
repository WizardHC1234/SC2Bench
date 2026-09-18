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
    from sc2bench_env.backends.sharpy.races.terran import get_adapter as get_terran_adapter
    return get_terran_adapter(race)
