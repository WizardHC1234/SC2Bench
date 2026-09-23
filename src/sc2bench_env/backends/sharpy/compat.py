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


def ensure_unit_type_id(value: int):
    """Return the enum member, creating one when this client is newer than burnysc2."""
    from sc2.ids.unit_typeid import UnitTypeId

    existing = UnitTypeId._value2member_map_.get(value)
    if existing is not None:
        return existing
    name = f"UNITTYPE_{value}"
    member = object.__new__(UnitTypeId)
    member._name_ = name
    member._value_ = value
    UnitTypeId._value2member_map_[value] = member
    UnitTypeId._member_map_[name] = member
    UnitTypeId._member_names_.append(name)
    return member


def install_unknown_unit_type_fallback() -> None:
    """Map doodads such as Acropolis unit 2009 must not abort startup."""
    from sc2.unit import Unit

    if getattr(Unit, "_sc2bench_unknown_types", False):
        return

    def type_id(self):
        cached = self.cache.get("type_id")
        if cached is not None:
            return cached
        unit_type = self._proto.unit_type
        known = self._bot_object._game_data.unit_types
        if unit_type not in known:
            known[unit_type] = ensure_unit_type_id(unit_type)
        self.cache["type_id"] = known[unit_type]
        return known[unit_type]

    Unit.type_id = property(type_id)
    Unit._sc2bench_unknown_types = True


def register_modern_terran_abilities() -> None:
    install_unknown_unit_type_fallback()
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
