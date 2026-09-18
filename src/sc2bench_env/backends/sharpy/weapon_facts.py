"""Shared active-weapon facts for observation and mission power estimates."""


def has_active_weapon(unit) -> bool:
    """Spell reach is not weapon capability; special automatic attacks remain.

    This is not a full tactical threat model: spell damage, hidden enemies and
    weapon cooldowns are left to Sharpy micro, not inferred as visible firepower.
    """
    name = getattr(getattr(unit, "type_id", None), "name", "")
    return (
        bool(getattr(unit, "is_ready", True))
        and float(getattr(unit, "health", 1)) > 0
        and (bool(getattr(unit, "can_attack", False))
             or name in {"CARRIER", "WIDOWMINEBURROWED"})
        and (name != "PHOTONCANNON" or bool(getattr(unit, "is_powered", False)))
    )
