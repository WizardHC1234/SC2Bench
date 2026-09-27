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
# Attack marches stay with the slower part of the army. A small cohort left
# far behind does not freeze everyone else.
MARCH_MAX_AHEAD = 8.0
MARCH_STRAGGLER_GAP = 22.0
MARCH_SLOW_SPEED_RATIO = 1.35
MARCH_SLOW_SHARE_KEEP = 0.25
# Any soldier this close to a visible enemy is already in the fight.
MARCH_CONTACT_RADIUS = 12.0


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


def march_speed(unit) -> float:
    """Movement speed used only to tell the slow body from faster units."""
    raw = getattr(unit, "movement_speed", None)
    try:
        speed = float(raw)
    except (TypeError, ValueError):
        return 1.0
    if speed != speed or speed <= 0.1:
        return 0.5
    return speed


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def _center(units) -> "Point2":
    from sc2.position import Point2

    count = len(units)
    return Point2((
        sum(float(unit.position.x) for unit in units) / count,
        sum(float(unit.position.y) for unit in units) / count,
    ))


def march_formation(units, target):
    """Units that have run too far ahead of an attack march, and where to wait.

    The anchor is the slower part of the army. One slow unit left far behind a
    much larger group is ignored. Returns (ahead, hold_point, rear_units).
    """
    members = [unit for unit in units if not getattr(unit, "is_structure", False)]
    if len(members) < 2:
        return [], None, []

    speeds = [march_speed(unit) for unit in members]
    slowest = min(speeds)
    slow_tags = {
        unit.tag for unit, speed in zip(members, speeds)
        if speed <= slowest * MARCH_SLOW_SPEED_RATIO
    }
    slow = [unit for unit in members if unit.tag in slow_tags]
    faster = [unit for unit in members if unit.tag not in slow_tags]
    if faster and len(slow) / len(members) < MARCH_SLOW_SHARE_KEEP:
        slow_distance = _median([unit.distance_to(target) for unit in slow])
        fast_distance = _median([unit.distance_to(target) for unit in faster])
        if slow_distance > fast_distance + MARCH_STRAGGLER_GAP:
            slow = []

    anchor = slow or members
    distances = [unit.distance_to(target) for unit in anchor]
    median = _median(distances)
    kept = [unit for unit, distance in zip(anchor, distances) if distance <= median + MARCH_STRAGGLER_GAP]
    if len(kept) < 2:
        kept = list(anchor)
    rear_distance = max(unit.distance_to(target) for unit in kept)
    if rear_distance <= MARCH_MAX_AHEAD:
        return [], None, kept
    rear_units = [unit for unit in kept if unit.distance_to(target) >= rear_distance - 1.5]
    if not rear_units:
        rear_units = kept
    rear_tags = {unit.tag for unit in rear_units}
    ahead = [
        unit for unit in members
        if unit.tag not in rear_tags
        and unit.distance_to(target) < rear_distance - MARCH_MAX_AHEAD
    ]
    if not ahead:
        return [], None, rear_units

    center = _center(rear_units)
    gap = center.distance_to(target)
    if gap <= 1.0:
        return [], None, rear_units
    hold = center.towards(target, min(MARCH_MAX_AHEAD * 0.5, gap - 0.5))
    return ahead, hold, rear_units
