"""Zerg combat forms and spells Sharpy does not register.

Transfusion, Corrosive Bile, Lurker burrow, Fungal Growth, Neural Parasite,
Locusts, Abduct, Blinding Cloud and Parasitic Bomb stay on Sharpy's handlers.
This file burrows Roaches and Banelings, lets Vipers consume, and keeps
burrowed forms on the same handler. Queen Inject is not cast.
"""
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.combat import Action, GenericMicro
from sharpy.combat.zerg import MicroOverseers, MicroVipers
from sc2bench_env.backends.sharpy.terran_micro import RETREAT, cast, visible_enemy

IMMOBILE_RELEASE = {
    UnitTypeId.LURKERMPBURROWED: AbilityId.BURROWUP_LURKER,
    UnitTypeId.INFESTORBURROWED: AbilityId.BURROWUP_INFESTOR,
    UnitTypeId.ROACHBURROWED: AbilityId.BURROWUP_ROACH,
    UnitTypeId.BANELINGBURROWED: AbilityId.BURROWUP_BANELING,
}


def _follow(micro, command):
    return Action(command.position if command.position is not None else micro.original_target, False)


def _enemies(micro):
    return [enemy for enemy in micro.enemies_near_by
            if visible_enemy(enemy) and not getattr(enemy, "is_structure", False)]


class MicroRoachSafe(GenericMicro):
    """Burrow a hurt Roach; surface once it can fight again."""

    def unit_solve_combat(self, unit, command):
        burrowed = unit.type_id == UnitTypeId.ROACHBURROWED or bool(getattr(unit, "is_burrowed", False))
        health = float(getattr(unit, "health_percentage", 1) or 1)
        if not burrowed and health < 0.4 and self.cd_manager.is_ready(unit.tag, AbilityId.BURROWDOWN_ROACH):
            return cast(self, unit, AbilityId.BURROWDOWN_ROACH)
        if burrowed and self.move_type not in RETREAT and health > 0.7:
            if self.cd_manager.is_ready(unit.tag, AbilityId.BURROWUP_ROACH):
                return cast(self, unit, AbilityId.BURROWUP_ROACH)
        if burrowed and self.move_type in RETREAT:
            return _follow(self, command)
        return super().unit_solve_combat(unit, command)


class MicroBanelingSafe(GenericMicro):
    """Burrow while leaving; surface when enemies are close enough to detonate."""

    def unit_solve_combat(self, unit, command):
        burrowed = unit.type_id == UnitTypeId.BANELINGBURROWED or bool(getattr(unit, "is_burrowed", False))
        enemies = _enemies(self)
        if self.move_type in RETREAT or not enemies:
            if not burrowed and self.move_type in RETREAT and self.cd_manager.is_ready(
                    unit.tag, AbilityId.BURROWDOWN_BANELING):
                return cast(self, unit, AbilityId.BURROWDOWN_BANELING)
            if burrowed and not enemies and self.cd_manager.is_ready(unit.tag, AbilityId.BURROWUP_BANELING):
                return cast(self, unit, AbilityId.BURROWUP_BANELING)
            return _follow(self, command)
        if burrowed and self.cd_manager.is_ready(unit.tag, AbilityId.BURROWUP_BANELING):
            return cast(self, unit, AbilityId.BURROWUP_BANELING)
        return super().unit_solve_combat(unit, command)


class MicroOverseerSafe(MicroOverseers):
    """Contaminate a nearby enemy building, then enter Oversight. Changelings stay on Sharpy."""

    def unit_solve_combat(self, unit, command):
        enemies = _enemies(self)
        # This client offers MORPH_OVERSIGHTMODE. MORPH_OVERSEERMODE is the other id.
        morph = next((
            ability for ability in (AbilityId.MORPH_OVERSIGHTMODE, AbilityId.MORPH_OVERSEERMODE)
            if self.cd_manager.is_ready(unit.tag, ability)
        ), None)
        if unit.type_id != UnitTypeId.OVERSEERSIEGEMODE and enemies and morph is not None:
            return cast(self, unit, morph)
        if float(getattr(unit, "energy", 0) or 0) >= 125 and self.cd_manager.is_ready(
                unit.tag, AbilityId.CONTAMINATE_CONTAMINATE):
            raw = getattr(self.ai, "enemy_structures", ()) or ()
            structures = [
                enemy for enemy in raw
                if not getattr(enemy, "is_snapshot", False)
                and not getattr(enemy, "is_memory", False)
                and getattr(enemy, "is_structure", False)
                and unit.distance_to(enemy) <= 7
            ]
            if structures:
                target = min(structures, key=lambda enemy: enemy.distance_to(unit))
                return cast(self, unit, AbilityId.CONTAMINATE_CONTAMINATE, target)
        return super().unit_solve_combat(unit, command)


class MicroViperSafe(MicroVipers):
    """Consume a nearby biological ally when energy is low, then use Sharpy spells."""

    def unit_solve_combat(self, unit, command):
        ability = self._consume_ability(unit)
        target = self._consume_target(unit) if ability is not None else None
        if (self.move_type not in RETREAT and float(getattr(unit, "energy", 0) or 0) < 50
                and ability is not None and target is not None):
            return cast(self, unit, ability, target)
        cloud = self._cloud_point(unit)
        if cloud is not None:
            return cast(self, unit, AbilityId.BLINDINGCLOUD_BLINDINGCLOUD, cloud)
        bomb = self._bomb_target(unit)
        if bomb is not None:
            ability, target = bomb
            return cast(self, unit, ability, target)
        return super().unit_solve_combat(unit, command)

    def _consume_ability(self, unit):
        # The structure entry is listed on this client but does not drain a unit.
        for ability in (
            AbilityId.VIPERCONSUME_VIPERCONSUME,
            AbilityId.VIPERCONSUMEMINERALS_VIPERCONSUME,
        ):
            if self.cd_manager.is_ready(unit.tag, ability):
                return ability
        return None

    def _consume_target(self, unit):
        pool = []
        group = getattr(self, "group", None)
        pool.extend(getattr(group, "units", ()) or ())
        pool.extend(getattr(getattr(self, "ai", None), "units", ()) or ())
        seen = set()
        for ally in pool:
            tag = getattr(ally, "tag", None)
            if tag in seen or tag == getattr(unit, "tag", None):
                continue
            seen.add(tag)
            if getattr(ally, "is_structure", False) or getattr(ally, "is_flying", False):
                continue
            if float(getattr(ally, "health", 0) or 0) <= 50:
                continue
            if unit.distance_to(ally) <= 4:
                return ally
        return None

    def _cloud_point(self, unit):
        # Sharpy only clouds units with power above 1, so a marine pack never qualifies,
        # and Abduct is checked first whenever a heavier unit is in range.
        if self.move_type in RETREAT or float(getattr(unit, "energy", 0) or 0) < 100:
            return None
        if not self.cd_manager.is_ready(unit.tag, AbilityId.BLINDINGCLOUD_BLINDINGCLOUD):
            return None
        ground = [
            enemy for enemy in _enemies(self)
            if not getattr(enemy, "is_flying", False) and unit.distance_to(enemy) <= 11
        ]
        if len(ground) < 6:
            return None
        return Point2((
            sum(enemy.position.x for enemy in ground) / len(ground),
            sum(enemy.position.y for enemy in ground) / len(ground),
        ))

    def _bomb_target(self, unit):
        # Sharpy checks Abduct first, so a pack of air units never gets the bomb.
        if self.move_type in RETREAT or float(getattr(unit, "energy", 0) or 0) < 125:
            return None
        ability = None
        for candidate in (
            AbilityId.PARASITICBOMB_PARASITICBOMB,
            AbilityId.VIPERPARASITICBOMBRELAY_PARASITICBOMB,
        ):
            if self.cd_manager.is_ready(unit.tag, candidate):
                ability = candidate
                break
        if ability is None:
            return None
        flyers = []
        seen = set()
        pool = list(_enemies(self))
        pool.extend(getattr(getattr(self, "ai", None), "enemy_units", ()) or ())
        for enemy in pool:
            tag = getattr(enemy, "tag", None)
            if tag in seen or not getattr(enemy, "is_flying", False):
                continue
            seen.add(tag)
            if unit.distance_to(enemy) <= 12:
                flyers.append(enemy)
        if len(flyers) < 4:
            return None
        return ability, flyers[0]
