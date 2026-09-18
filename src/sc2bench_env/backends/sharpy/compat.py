"""Missing multiplayer ability IDs in the bundled legacy burnysc2 enums.

Only fill vacant IDs, never remap existing members. Register before GameData
filters the response, so research orders and available-ability queries work.
IDs verified against the installed SC2 5.0.16 ResponseData.
"""
from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId


def has_antiarmor_debuff(unit, full_version: str = "") -> bool:
    """Recognize observed debuffs, never missile-target tint as a landed hit.

    Installed 5.0.16.97563: isolated passive targets acquire raw buff 300
    after missile-target tint 279; old burnysc2 only names legacy debuff 280.
    This empirical alias is exact-build scoped, not a global enum remapping.
    Other clients need their own validation before extending the alias.
    """
    if unit.has_buff(BuffId.RAVENSHREDDERMISSILEARMORREDUCTION):
        return True
    proto = getattr(unit, "_proto", None)
    return full_version == "5.0.16.97563" and 300 in getattr(proto, "buff_ids", ())


def register_modern_terran_abilities() -> None:
    for name, value in (
        ("RESEARCH_INTERFERENCEMATRIX", 807),
        ("FUSIONCORERESEARCH_RESEARCHMEDIVACENERGYREGENERATION", 1535),
    ):
        if value in AbilityId._value2member_map_:
            continue
        member = object.__new__(AbilityId)
        member._name_ = name
        member._value_ = value
        AbilityId._value2member_map_[value] = member
        AbilityId._member_map_[name] = member
        AbilityId._member_names_.append(name)
    # Legacy AbilityData caches an import-time sorted index separately.
    from sc2.game_data import AbilityData
    AbilityData.ability_ids = sorted(set(AbilityData.ability_ids) | {807, 1535})
