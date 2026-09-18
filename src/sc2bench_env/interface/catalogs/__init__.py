"""Select implemented race catalogs without importing game libraries."""

from sc2bench_env.interface.catalog_types import CatalogData
from sc2bench_env.interface.races import require_supported_own_race


def get_catalog(*, race: str = "terran") -> CatalogData:
    require_supported_own_race(race)
    if race == "terran":
        from sc2bench_env.interface.catalogs.terran import CATALOG

        return CATALOG
    # Extending the support list alone must never fall back to Terran data.
    raise ValueError(f"No action catalog implemented for own race {race!r}")
