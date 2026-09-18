"""Platform-owned Terran micro extensions used by combat missions.

Stim / Tank reuse Sharpy defaults. Banshee cloak and Medivac heal/escort
fill gaps called out in PLATFORM_PLAN and the original encapsulation notes.
Transport load/unload is owned by ActCombatMission, not these micros.
"""

from __future__ import annotations

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units
from sharpy.combat import Action, GenericMicro, MicroRules, MicroStep
from sharpy.combat.move_type import MoveType


class MicroBanshee(GenericMicro):
    """Cloak when energy allows; decloak before energy collapses."""

    CLOAK_ON_ENERGY = 50.0
    CLOAK_OFF_ENERGY = 15.0

    def unit_solve_combat(self, unit: Unit, current_command: Action) -> Action:
        if unit.type_id != UnitTypeId.BANSHEE:
            return super().unit_solve_combat(unit, current_command)

        cloaked = bool(getattr(unit, "is_cloaked", False))
        energy = float(getattr(unit, "energy", 0.0) or 0.0)

        if cloaked and energy <= self.CLOAK_OFF_ENERGY:
            if (self.cd_manager.is_ready(unit.tag, AbilityId.BEHAVIOR_CLOAKOFF_BANSHEE)
                    or self.cd_manager.is_ready(unit.tag, AbilityId.BEHAVIOR_CLOAKOFF)):
                return Action(None, False, AbilityId.BEHAVIOR_CLOAKOFF_BANSHEE)

        if (not cloaked) and energy >= self.CLOAK_ON_ENERGY:
            # A failed cast would overwrite movement every frame. Check cached
            # availability (including research) before issuing the ability.
            if (self.cd_manager.is_ready(unit.tag, AbilityId.BEHAVIOR_CLOAKON_BANSHEE)
                    or self.cd_manager.is_ready(unit.tag, AbilityId.BEHAVIOR_CLOAKON)):
                return Action(None, False, AbilityId.BEHAVIOR_CLOAKON_BANSHEE)

        return super().unit_solve_combat(unit, current_command)


class MicroMedivacsSupport(MicroStep):
    """Heal concrete bio targets and escort ground; no Point2 'attack' heal spam."""

    HEAL_ENERGY_FLOOR = 5
    ESCORT_OFFSET = 2.5

    def group_solve_combat(self, units: Units, current_command: Action) -> Action:
        return current_command

    def unit_solve_combat(self, unit: Unit, current_command: Action) -> Action:
        if self.move_type in {MoveType.DefensiveRetreat, MoveType.PanicRetreat}:
            # Do not turn a mission retreat into healing/escorting troops that
            # have not retreated yet (or unrelated troops elsewhere).
            return Action(current_command.position if current_command.position is not None else self.original_target, False)
        healable = self.group.ground_units.filter(self._is_healable)
        if unit.energy >= self.HEAL_ENERGY_FLOOR and healable:
            return Action(
                healable.closest_to(unit),
                False,
                AbilityId.MEDIVACHEAL_HEAL,
            )

        escort = self._escort_point(unit, current_command)
        if self.enemies_near_by:
            escort = self.pather.find_weak_influence_air(escort, 8)
        return Action(escort, False)

    @staticmethod
    def _is_healable(target: Unit) -> bool:
        return (
            target.health_percentage < 1
            and not target.is_flying
            and (target.is_biological or target.type_id == UnitTypeId.HELLIONTANK)
        )

    def _escort_point(self, unit: Unit, current_command: Action) -> Point2:
        ground = self.group.ground_units
        if ground:
            anchor = self.group.center
        else:
            # Air and ground can be split into different combat groups. Do not
            # abandon this mission to escort unrelated troops from all_own.
            if current_command.position is not None:
                anchor = current_command.position
            else:
                anchor = unit.position

        target = current_command.position
        if target is not None and anchor.distance_to(target) > 0.1:
            anchor = anchor.towards(target, -self.ESCORT_OFFSET)

        x_offset = (unit.tag % 5 - 2) * 0.55
        y_offset = (unit.tag % 7 - 3) * 0.45
        return anchor + Point2((x_offset, y_offset))


class MissionMicroRules(MicroRules):
    """Apply mission boundaries AFTER specialized micro chooses its command.

    Changing only the group destination does not stop focus-fire chasing.
    Rules are per mission, so the guard never changes global Sharpy defaults.
    """

    def __init__(self):
        super().__init__()
        self.boundary = None
        self.return_point = None

    async def start(self, knowledge):
        await super().start(knowledge)
        seen = set()
        for micro in [self.generic_micro, *self.unit_micros.values()]:
            if id(micro) in seen:
                continue
            seen.add(id(micro))
            solve = micro.unit_solve_combat

            def guarded(unit, command, solve=solve):
                return self.guard_action(unit, solve(unit, command))

            micro.unit_solve_combat = guarded

    def guard_action(self, unit, command):
        if self.boundary is None or self.return_point is None:
            return command
        outside = not self.boundary(unit.position)
        target_outside = command.position is not None and not self.boundary(command.position)
        if outside or target_outside:
            from sc2bench_env.backends.sharpy.terran_micro import IMMOBILE_RELEASE
            if unit.type_id in IMMOBILE_RELEASE:
                return Action(None, False, IMMOBILE_RELEASE[unit.type_id])
            return Action(self.return_point, False)
        return command


def build_combat_micro_rules() -> MicroRules:
    """Default Sharpy micro plus platform Banshee / Medivac / sieged-tank handlers."""
    from sharpy.combat.terran import MicroTanks

    rules = MissionMicroRules()
    rules.load_default_methods()
    rules.load_default_micro()
    tanks = MicroTanks()
    rules.unit_micros[UnitTypeId.SIEGETANK] = tanks
    rules.unit_micros[UnitTypeId.SIEGETANKSIEGED] = tanks
    rules.unit_micros[UnitTypeId.BANSHEE] = MicroBanshee()
    rules.unit_micros[UnitTypeId.MEDIVAC] = MicroMedivacsSupport()
    from sc2bench_env.backends.sharpy.terran_micro import (
        MicroGhost, MicroCyclone, MicroThor, MicroWidowMine, MicroLiberatorSafe,
        MicroVikingSafe, MicroRavenSupport, MicroBattlecruiserSafe,
    )
    for types, micro in (
        ((UnitTypeId.GHOST,), MicroGhost()), ((UnitTypeId.CYCLONE,), MicroCyclone()),
        ((UnitTypeId.THOR, UnitTypeId.THORAP), MicroThor()),
        ((UnitTypeId.WIDOWMINE, UnitTypeId.WIDOWMINEBURROWED), MicroWidowMine()),
        ((UnitTypeId.LIBERATOR, UnitTypeId.LIBERATORAG), MicroLiberatorSafe()),
        ((UnitTypeId.VIKINGFIGHTER, UnitTypeId.VIKINGASSAULT), MicroVikingSafe()),
        ((UnitTypeId.RAVEN,), MicroRavenSupport()),
        ((UnitTypeId.BATTLECRUISER,), MicroBattlecruiserSafe()),
    ):
        for unit_type in types:
            rules.unit_micros[unit_type] = micro
    return rules
