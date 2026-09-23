"""First-edition engineering defaults for combat missions.

Confirmed behavioral boundaries live in PLATFORM_PLAN §3.2.
These are reproducible initial defaults, not empirically optimal SC2 values.
Behavioral acceptance still requires real combat scenarios. PROVISIONAL names
are retained for compatibility with existing callers.
"""

from __future__ import annotations

PROVISIONAL_DEFEND_LEASH = 1.25  # × zone radius
TRANSPORT_STANDOFF = 8.0
TRANSPORT_MIN_TRAVEL = 24.0
TRANSPORT_CONTACT_RADIUS = 15.0
PROVISIONAL_WITHDRAW_ARRIVAL = 10.0
DROP_LOAD_TIMEOUT_SECONDS = 10.0
DROP_UNLOAD_TIMEOUT_SECONDS = 10.0
TARGET_CLEAR_CONFIRM_SECONDS = 3.0


def available_for_mission(unit, roles, reserved_tags) -> bool:
    """One availability rule shared by real binding and Observation counts."""
    from sharpy.managers.core.roles import UnitTask

    if unit.tag in reserved_tags or unit.is_structure or not unit.is_ready or getattr(unit, "is_hallucination", False):
        return False
    if roles is None:
        return True
    if unit.tag in getattr(getattr(roles, "ai", None), "bench_group0_tags", set()):
        return not roles.is_in_role(UnitTask.Scouting, unit)
    return not any(roles.is_in_role(role, unit) for role in (
        UnitTask.Attacking, UnitTask.Defending, UnitTask.Fighting,
        UnitTask.Reserved, UnitTask.Scouting,
    ))


def style_move_type_name(style: str) -> str:
    return {
        "attack": "Assault",
        # This Sharpy version has no MoveType.Hold. MissionMicroRules supplies
        # equivalent hold behavior after Assault micro proposes each command.
        "defend": "Assault",
    }.get(style, "Assault")


def transport_drop_point(zone_center, own_start, *, standoff: float = TRANSPORT_STANDOFF):
    """Point on the line from enemy zone toward our start (do not dive the base)."""
    if zone_center.distance_to(own_start) <= 1e-3:
        return zone_center
    # Point2.towards does not clamp: a short zone-to-home distance must not
    # produce a hold point behind our home (or negative standoff dive forward).
    distance = zone_center.distance_to(own_start)
    return zone_center.towards(own_start, min(distance, max(0.0, standoff)))


def defend_leash_radius(zone_radius: float, leash_factor: float = PROVISIONAL_DEFEND_LEASH) -> float:
    return max(8.0, float(zone_radius) * leash_factor)
