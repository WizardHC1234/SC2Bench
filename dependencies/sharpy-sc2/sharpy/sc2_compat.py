"""Compatibility shims for python-sc2 / StarCraft II version mismatches."""

from __future__ import annotations

import logging
from typing import Dict, List, Set, Union

logger = logging.getLogger("SC2_Agent.sc2_compat")

_ORDER_PATCHED = False
_ABILITIES_PATCHED = False
_BUFFS_PATCHED = False


def patch_unknown_ability_orders() -> None:
    """Install all SC2 ability compatibility patches used by this bot."""
    _patch_unknown_ability_orders()
    _patch_unknown_available_abilities()
    _patch_unknown_buff_ids()


def _patch_unknown_ability_orders() -> None:
    """Prevent KeyError when unit orders reference ability ids missing from GameData.

    Newer SC2 builds can put ability ids such as 4135 on worker build-gas orders
    while burnysc2's loaded ``game_data.abilities`` map does not contain them.
    Accessing ``unit.orders`` then crashes distribute_workers / build acts.
    """
    global _ORDER_PATCHED
    if _ORDER_PATCHED:
        return

    from sc2.unit import UnitOrder

    original = UnitOrder.from_proto.__func__

    @classmethod
    def from_proto(cls, proto, bot_object):  # type: ignore[no-untyped-def]
        abilities = bot_object._game_data.abilities
        ability_id = int(proto.ability_id)
        if ability_id not in abilities:
            # Fall back to SMART (1) or any known ability so order parsing continues.
            ability = abilities.get(1) or next(iter(abilities.values()))
            logger.debug(
                "Unknown ability_id=%s in unit order; using fallback ability %s",
                ability_id,
                getattr(getattr(ability, "id", None), "name", ability),
            )
        else:
            ability = abilities[ability_id]
        target = (
            proto.target_world_space_pos
            if proto.HasField("target_world_space_pos")
            else proto.target_unit_tag
        )
        return cls(ability, target, proto.progress)

    UnitOrder.from_proto = from_proto  # type: ignore[assignment]
    # Keep a reference so the original is not GC'd / for debugging.
    UnitOrder._sc2_agent_original_from_proto = staticmethod(original)  # type: ignore[attr-defined]
    _ORDER_PATCHED = True


def _safe_ability_ids(raw_abilities) -> List:
    """Convert proto ability ids, skipping values missing from AbilityId."""
    from sc2.ids.ability_id import AbilityId

    parsed = []
    for ability in raw_abilities:
        ability_id = int(getattr(ability, "ability_id", ability))
        try:
            parsed.append(AbilityId(ability_id))
        except ValueError:
            logger.debug("Skipping unknown AbilityId=%s from available abilities", ability_id)
    return parsed


def _patch_unknown_available_abilities() -> None:
    """Skip unknown ability ids returned by QueryAvailableAbilities.

    SC2 can return ability ids (for example 807 = Raven Interference Matrix
    research) that an older burnysc2 ``AbilityId`` enum does not contain.
    Without this patch, ``CooldownManager`` fails the whole abilities query for
    that frame and logs ``Get available abilities failed``.
    """
    global _ABILITIES_PATCHED
    if _ABILITIES_PATCHED:
        return

    from sc2.client import Client
    from sc2.ids.ability_id import AbilityId
    from sc2.unit import Unit
    from sc2.units import Units
    from s2clientprotocol import query_pb2 as query_pb

    async def query_available_abilities(
        self,
        units: Union[List[Unit], Units],
        ignore_resource_requirements: bool = False,
    ) -> List[List[AbilityId]]:
        input_was_a_list = True
        if not isinstance(units, list):
            assert isinstance(units, Unit)
            units = [units]
            input_was_a_list = False
        assert units
        result = await self._execute(
            query=query_pb.RequestQuery(
                abilities=(
                    query_pb.RequestQueryAvailableAbilities(unit_tag=unit.tag)
                    for unit in units
                ),
                ignore_resource_requirements=ignore_resource_requirements,
            )
        )
        parsed = [
            _safe_ability_ids(unit_abilities.abilities)
            for unit_abilities in result.query.abilities
        ]
        if not input_was_a_list:
            return parsed[0]
        return parsed

    async def query_available_abilities_with_tag(
        self,
        units: Union[List[Unit], Units],
        ignore_resource_requirements: bool = False,
    ) -> Dict[int, Set[AbilityId]]:
        result = await self._execute(
            query=query_pb.RequestQuery(
                abilities=(
                    query_pb.RequestQueryAvailableAbilities(unit_tag=unit.tag)
                    for unit in units
                ),
                ignore_resource_requirements=ignore_resource_requirements,
            )
        )
        return {
            unit_abilities.unit_tag: set(_safe_ability_ids(unit_abilities.abilities))
            for unit_abilities in result.query.abilities
        }

    Client.query_available_abilities = query_available_abilities  # type: ignore[assignment]
    Client.query_available_abilities_with_tag = (  # type: ignore[assignment]
        query_available_abilities_with_tag
    )
    _ABILITIES_PATCHED = True


def _patch_unknown_buff_ids() -> None:
    """Ignore unknown enum values without losing known observed buffs."""
    global _BUFFS_PATCHED
    if _BUFFS_PATCHED:
        return
    from sc2.cache import property_immutable_cache
    from sc2.ids.buff_id import BuffId
    from sc2.unit import Unit

    @property_immutable_cache
    def buffs(self) -> Set[BuffId]:
        parsed: Set[BuffId] = set()
        for buff_id in self._proto.buff_ids:
            try:
                parsed.add(BuffId(int(buff_id)))
            except ValueError:
                logger.debug("Skipping unknown BuffId=%s on unit %s", buff_id, self.tag)
        return parsed

    Unit.buffs = buffs
    _BUFFS_PATCHED = True
