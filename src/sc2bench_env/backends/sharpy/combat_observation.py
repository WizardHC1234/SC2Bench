"""Observable army activity, independent of tactical decisions."""

GROUP_ENEMY_OBSERVATION_RADIUS = 15.0


def observe_group_activity(members, enemy_units, enemy_structures):
    """Count visible enemies near any on-map member, never fog memory.

    Weapon cooldown is evidence of recent weapon use, not a judgement that
    combat is ongoing. Loaded passengers have no on-map position and are not
    included; their carrier is included when it belongs to this group.
    """
    members = list(members)
    if not members:
        return {"visible_enemy_nearby": None, "weapon_cooldown_active_count": None}

    def count_nearby(enemies):
        tags = set()
        for enemy in enemies:
            if (not bool(getattr(enemy, "is_visible", False))
                    or any(bool(getattr(enemy, flag, False)) for flag in
                           ("is_snapshot", "is_memory", "is_hallucination"))):
                continue
            if any(member.distance_to(enemy.position) <= GROUP_ENEMY_OBSERVATION_RADIUS
                   for member in members):
                tags.add(enemy.tag)
        return len(tags)

    cooldowns = [getattr(member, "weapon_cooldown", None) for member in members]
    return {
        "visible_enemy_nearby": {
            "units": count_nearby(enemy_units),
            "buildings": count_nearby(enemy_structures),
        },
        "weapon_cooldown_active_count": (
            sum(value > 0 for value in cooldowns)
            if all(value is not None for value in cooldowns) else None
        ),
    }
