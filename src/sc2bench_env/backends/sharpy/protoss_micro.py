"""Protoss micro that Sharpy's defaults do not cover safely.

Blink, Charge, Shade, Force Field, Guardian Shield, Hallucination, Storm,
Feedback, Purification Nova, Graviton Beam, Pulsar Beam and Prismatic
Alignment stay on Sharpy's handlers. This file adds the missing casts and
replaces Warp Prism micro so it does not load, unload or warp units.
"""
from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.combat import Action, GenericMicro, MoveType
from sharpy.combat.action import NoAction
from sharpy.combat.protoss import MicroOracles
from sc2bench_env.backends.sharpy.terran_micro import RETREAT, cast, visible_enemy

IMMOBILE_RELEASE = {
    UnitTypeId.OBSERVERSIEGEMODE: AbilityId.MORPH_OBSERVERMODE,
    UnitTypeId.WARPPRISMPHASING: AbilityId.MORPH_WARPPRISMTRANSPORTMODE,
}


def _enemies(micro):
    return [enemy for enemy in micro.enemies_near_by
            if visible_enemy(enemy) and not getattr(enemy, "is_structure", False)]


def _center(units):
    return Point2((
        sum(unit.position.x for unit in units) / len(units),
        sum(unit.position.y for unit in units) / len(units),
    ))


def _follow(micro, command):
    return Action(command.position if command.position is not None else micro.original_target, False)


class MicroObserverSafe(GenericMicro):
    """Surveillance while enemies are close; release the form to move or retreat."""

    def unit_solve_combat(self, unit, command):
        sieged = unit.type_id == UnitTypeId.OBSERVERSIEGEMODE
        enemies = _enemies(self)
        if self.move_type in RETREAT or not enemies:
            if sieged and self.cd_manager.is_ready(unit.tag, AbilityId.MORPH_OBSERVERMODE):
                return cast(self, unit, AbilityId.MORPH_OBSERVERMODE)
            return _follow(self, command)
        if not sieged and self.cd_manager.is_ready(unit.tag, AbilityId.MORPH_SURVEILLANCEMODE):
            return cast(self, unit, AbilityId.MORPH_SURVEILLANCEMODE)
        return NoAction() if sieged else _follow(self, command)


class MicroWarpPrismSafe(GenericMicro):
    """Phase during contact and transport otherwise. Cargo stays with the mission."""

    def unit_solve_combat(self, unit, command):
        phased = unit.type_id == UnitTypeId.WARPPRISMPHASING
        enemies = _enemies(self)
        if self.move_type in RETREAT or not enemies:
            if phased and self.cd_manager.is_ready(unit.tag, AbilityId.MORPH_WARPPRISMTRANSPORTMODE):
                return cast(self, unit, AbilityId.MORPH_WARPPRISMTRANSPORTMODE)
            return _follow(self, command)
        if not phased and self.cd_manager.is_ready(unit.tag, AbilityId.MORPH_WARPPRISMPHASINGMODE):
            return cast(self, unit, AbilityId.MORPH_WARPPRISMPHASINGMODE)
        return NoAction() if phased else _follow(self, command)


class MicroOracleSafe(MicroOracles):
    """Revelation on a cluster, a stasis ward when leaving, then Sharpy's beam."""

    def unit_solve_combat(self, unit, command):
        if self.move_type in RETREAT:
            if getattr(unit, "has_buff", lambda _buff: False)(BuffId.ORACLEWEAPON):
                return cast(self, unit, AbilityId.BEHAVIOR_PULSARBEAMOFF)
            ward = self._stasis_point(unit)
            if ward is not None:
                return cast(self, unit, self._stasis_ability(unit), ward)
            return _follow(self, command)
        enemies = _enemies(self)
        if (len(enemies) >= 3 and float(getattr(unit, "energy", 0) or 0) >= 50
                and self.cd_manager.is_ready(unit.tag, AbilityId.ORACLEREVELATION_ORACLEREVELATION)):
            return cast(self, unit, AbilityId.ORACLEREVELATION_ORACLEREVELATION, _center(enemies))
        # Sharpy stays to beam any light unit within 10, so an outbound oracle
        # never leaves the first marine at home.
        destination = command.position
        close = [enemy for enemy in enemies if enemy.distance_to(unit) <= 6]
        if destination is not None and unit.distance_to(destination) > 12 and len(close) < 3:
            return _follow(self, command)
        return super().unit_solve_combat(unit, command)

    def _stasis_ability(self, unit):
        # This client offers BUILD_STASISTRAP. The longer id is the other spell entry.
        for ability in (
            AbilityId.BUILD_STASISTRAP,
            AbilityId.ORACLESTASISTRAP_ORACLEBUILDSTASISTRAP,
        ):
            if self.cd_manager.is_ready(unit.tag, ability):
                return ability
        return None

    def _stasis_point(self, unit):
        if float(getattr(unit, "energy", 0) or 0) < 50:
            return None
        if self._stasis_ability(unit) is None:
            return None
        ground = [enemy for enemy in _enemies(self) if not enemy.is_flying]
        if len(ground) < 3:
            return None
        return _center(ground)


class MicroDarkTemplarSafe(GenericMicro):
    """Short blink after Shadow Stride is researched. Permanent cloak stays passive."""

    def unit_solve_combat(self, unit, command):
        if not self.cd_manager.is_ready(unit.tag, AbilityId.EFFECT_SHADOWSTRIDE):
            return super().unit_solve_combat(unit, command)
        enemies = _enemies(self)
        if not enemies:
            return super().unit_solve_combat(unit, command)
        nearest = min(enemies, key=lambda enemy: enemy.distance_to(unit))
        health = float(getattr(unit, "health_percentage", 1) or 1)
        if self.move_type in RETREAT or health < 0.4:
            away = nearest.position.towards(unit.position, nearest.distance_to(unit) + 6)
            return cast(self, unit, AbilityId.EFFECT_SHADOWSTRIDE, away)
        if nearest.distance_to(unit) > 2:
            step = min(6.0, nearest.distance_to(unit) - 0.5)
            return cast(self, unit, AbilityId.EFFECT_SHADOWSTRIDE, unit.position.towards(nearest.position, step))
        return super().unit_solve_combat(unit, command)


class MicroCarrierSafe(GenericMicro):
    """Build interceptors when the game still offers the ability. Do not build while running."""

    def unit_solve_combat(self, unit, command):
        if (self.move_type not in RETREAT
                and self.cd_manager.is_ready(unit.tag, AbilityId.BUILD_INTERCEPTORS)):
            return cast(self, unit, AbilityId.BUILD_INTERCEPTORS)
        return super().unit_solve_combat(unit, command)


class MicroMothership(GenericMicro):
    """Time Warp a cluster. Mass Recall stays unused."""

    def unit_solve_combat(self, unit, command):
        if self.move_type in RETREAT:
            return _follow(self, command)
        enemies = _enemies(self)
        energy = float(getattr(unit, "energy", 0) or 0)
        energy_max = float(getattr(unit, "energy_max", 0) or 0)
        # A debug-created mothership on this client reports no energy pool, while
        # the game still offers Time Warp. Trust that offer. A real pool still
        # has to hold 100.
        enough = energy >= 100 or energy_max <= 0
        if (len(enemies) >= 4 and enough
                and self.cd_manager.is_ready(unit.tag, AbilityId.EFFECT_TIMEWARP)):
            return cast(self, unit, AbilityId.EFFECT_TIMEWARP, _center(enemies))
        return super().unit_solve_combat(unit, command)
