"""Toggle Overlord Generate Creep only while a stationed overlord is holding still.

Home supply Overlords are not touched. Scout, transport, combat movement and
retreat turn the behavior off before any move order.
"""
from __future__ import annotations

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId

CREEP_OVERLORDS = frozenset({
    UnitTypeId.OVERLORD,
    UnitTypeId.OVERLORDTRANSPORT,
})
CREEP_ON = AbilityId.BEHAVIOR_GENERATECREEPON
CREEP_OFF = AbilityId.BEHAVIOR_GENERATECREEPOFF


def is_creep_overlord(unit) -> bool:
    return getattr(unit, "type_id", None) in CREEP_OVERLORDS


def _tags(ai):
    tags = getattr(ai, "bench_creep_tags", None)
    if tags is None:
        tags = set()
        setattr(ai, "bench_creep_tags", tags)
    return tags


def maintain_station_creep(ai, unit, hold: bool) -> str:
    """Return holding, toggled, or clear.

    holding and toggled mean this frame must not also issue a move.
    """
    if not is_creep_overlord(unit):
        return "clear"
    tags = _tags(ai)
    active = int(unit.tag) in tags
    if hold and not active:
        unit(CREEP_ON, None)
        tags.add(int(unit.tag))
        return "toggled"
    if not hold and active:
        unit(CREEP_OFF, None)
        tags.discard(int(unit.tag))
        return "toggled"
    if hold and active:
        return "holding"
    return "clear"
