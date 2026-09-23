"""Current race support boundary, independent of optional SC2/Sharpy imports.

Configuring an enemy race does not enable that race for our own production.
Add an own race only after its catalog, executor and observations are connected.
"""

SUPPORTED_OWN_RACES = ("terran", "protoss", "zerg")
ENEMY_RACES = ("terran", "protoss", "zerg", "random")


def require_supported_own_race(race: str) -> None:
    """Require a canonical, implemented own-race ID; never fall back to Terran."""
    if not isinstance(race, str) or race not in SUPPORTED_OWN_RACES:
        raise ValueError(
            f"Unsupported own race {race!r}; available: {', '.join(SUPPORTED_OWN_RACES)}. "
            "Other own races are not implemented."
        )
