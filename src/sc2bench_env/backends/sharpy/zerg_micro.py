"""Zerg combat forms and spells Sharpy does not register.

Transfusion, Corrosive Bile, Lurker burrow, Fungal Growth, Neural Parasite,
Locusts, Abduct, Blinding Cloud and Parasitic Bomb stay on Sharpy's handlers.
This file burrows Roaches and Banelings, lets Vipers consume buildings, and
keeps burrowed forms on the same handler. Queen Inject is not cast. Drones
are never burrowed for mining or construction.
"""
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.combat import Action, GenericMicro
from sharpy.combat.action import NoAction
from sharpy.combat.zerg import MicroOverseers, MicroVipers
from sc2bench_env.backends.sharpy.terran_micro import RETREAT, cast, visible_enemy

# Consume removes about 150 life. A legal target must survive that.
CONSUME_DAMAGE = 150.0
CONSUME_RANGE = 7.0
CONSUME_ENERGY_LIMIT = 50.0
_CONSUME_ABILITY = AbilityId.VIPERCONSUME_VIPERCONSUME

def _zerg_type(name: str):
    return getattr(UnitTypeId, name, None)


_TOWNHALLS = frozenset(item for item in (
    _zerg_type("HATCHERY"), _zerg_type("LAIR"), _zerg_type("HIVE"),
) if item is not None)
_LOW_VALUE = frozenset(item for item in (
    _zerg_type("SPINECRAWLER"), _zerg_type("SPORECRAWLER"), _zerg_type("EXTRACTOR"),
    _zerg_type("CREEPTUMOR"), _zerg_type("CREEPTUMORBURROWED"), _zerg_type("CREEPTUMORQUEEN"),
) if item is not None)
_UNIQUE_TECH = frozenset(item for item in (
    _zerg_type("SPAWNINGPOOL"), _zerg_type("EVOLUTIONCHAMBER"), _zerg_type("ROACHWARREN"),
    _zerg_type("BANELINGNEST"), _zerg_type("HYDRALISKDEN"), _zerg_type("LURKERDENMP"),
    _zerg_type("INFESTATIONPIT"), _zerg_type("SPIRE"), _zerg_type("GREATERSPIRE"),
    _zerg_type("ULTRALISKCAVERN"), _zerg_type("NYDUSNETWORK"), _zerg_type("NYDUSCANAL"),
) if item is not None)
ZERG_CONSUME_STRUCTURES = _TOWNHALLS | _LOW_VALUE | _UNIQUE_TECH
_MORPH_ABILITIES = frozenset(item for item in (
    getattr(AbilityId, "MORPH_LAIR", None),
    getattr(AbilityId, "MORPH_HIVE", None),
    getattr(AbilityId, "UPGRADETOGREATERSPIRE_GREATERSPIRE", None),
    getattr(AbilityId, "MORPH_GREATERSPIRE", None),
) if item is not None)

IMMOBILE_RELEASE = {
    UnitTypeId.BANELINGBURROWED: AbilityId.BURROWUP_BANELING,
    UnitTypeId.HYDRALISKBURROWED: AbilityId.BURROWUP_HYDRALISK,
    UnitTypeId.INFESTORBURROWED: AbilityId.BURROWUP_INFESTOR,
    UnitTypeId.LURKERMPBURROWED: AbilityId.BURROWUP_LURKER,
    UnitTypeId.QUEENBURROWED: AbilityId.BURROWUP_QUEEN,
    UnitTypeId.RAVAGERBURROWED: AbilityId.BURROWUP_RAVAGER,
    UnitTypeId.ROACHBURROWED: AbilityId.BURROWUP_ROACH,
    UnitTypeId.SWARMHOSTBURROWEDMP: AbilityId.BURROWUP_SWARMHOST,
    UnitTypeId.ULTRALISKBURROWED: AbilityId.BURROWUP_ULTRALISK,
    UnitTypeId.ZERGLINGBURROWED: AbilityId.BURROWUP_ZERGLING,
    UnitTypeId.OVERSEERSIEGEMODE: AbilityId.MORPH_OVERSEERMODE,
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
        if self.move_type in RETREAT:
            if burrowed and self.cd_manager.is_ready(unit.tag, AbilityId.BURROWUP_ROACH):
                return cast(self, unit, AbilityId.BURROWUP_ROACH)
            if burrowed:
                return NoAction()
            return _follow(self, command)
        if not burrowed and health < 0.4 and self.cd_manager.is_ready(unit.tag, AbilityId.BURROWDOWN_ROACH):
            return cast(self, unit, AbilityId.BURROWDOWN_ROACH)
        if burrowed and health > 0.7:
            if self.cd_manager.is_ready(unit.tag, AbilityId.BURROWUP_ROACH):
                return cast(self, unit, AbilityId.BURROWUP_ROACH)
        return super().unit_solve_combat(unit, command)


class MicroBanelingSafe(GenericMicro):
    """Keep Banelings mobile; surface before moving or engaging."""

    def unit_solve_combat(self, unit, command):
        burrowed = unit.type_id == UnitTypeId.BANELINGBURROWED or bool(getattr(unit, "is_burrowed", False))
        enemies = _enemies(self)
        if self.move_type in RETREAT or not enemies:
            if burrowed and self.cd_manager.is_ready(unit.tag, AbilityId.BURROWUP_BANELING):
                return cast(self, unit, AbilityId.BURROWUP_BANELING)
            if burrowed:
                return NoAction()
            return _follow(self, command)
        if burrowed and self.cd_manager.is_ready(unit.tag, AbilityId.BURROWUP_BANELING):
            return cast(self, unit, AbilityId.BURROWUP_BANELING)
        return super().unit_solve_combat(unit, command)


class MicroOverseerSafe(MicroOverseers):
    """Contaminate a nearby enemy building, then enter Oversight. Changelings stay on Sharpy."""

    def unit_solve_combat(self, unit, command):
        enemies = _enemies(self)
        oversight = unit.type_id == UnitTypeId.OVERSEERSIEGEMODE
        if self.move_type in RETREAT:
            if oversight and self.cd_manager.is_ready(unit.tag, AbilityId.MORPH_OVERSEERMODE):
                return cast(self, unit, AbilityId.MORPH_OVERSEERMODE)
            return NoAction() if oversight else _follow(self, command)
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
        if not enemies:
            if oversight and self.cd_manager.is_ready(unit.tag, AbilityId.MORPH_OVERSEERMODE):
                return cast(self, unit, AbilityId.MORPH_OVERSEERMODE)
            return NoAction() if oversight else _follow(self, command)
        if not oversight and self.cd_manager.is_ready(unit.tag, AbilityId.MORPH_OVERSIGHTMODE):
            return cast(self, unit, AbilityId.MORPH_OVERSIGHTMODE)
        return super().unit_solve_combat(unit, command)


class MicroEscapeBurrow(GenericMicro):
    """Burrow an ordinary fighter only to break contact or heal, then surface to move."""

    burrowed_type = None
    down_ability = None
    up_ability = None

    def unit_solve_combat(self, unit, command):
        burrowed = unit.type_id == self.burrowed_type or bool(getattr(unit, "is_burrowed", False))
        health = float(getattr(unit, "health_percentage", 1) or 1)
        enemies = _enemies(self)
        in_contact = any(unit.distance_to(enemy) <= 6 for enemy in enemies)
        if self.move_type in RETREAT or health > 0.7:
            if burrowed and self.cd_manager.is_ready(unit.tag, self.up_ability):
                return cast(self, unit, self.up_ability)
            if burrowed:
                return NoAction()
            return _follow(self, command)
        # Clear gain: leave a fight while hurt, or hold still to regenerate.
        if not burrowed and health < 0.4 and (in_contact or not enemies):
            if self.cd_manager.is_ready(unit.tag, self.down_ability):
                return cast(self, unit, self.down_ability)
        if burrowed:
            return NoAction()
        return super().unit_solve_combat(unit, command)


def guard_retreat_unburrow(handler, up_ability, burrowed_type) -> None:
    """Surface a burrowed fighter before retreat movement replaces the order."""
    original = handler.unit_solve_combat

    def wrapped(unit, command, _original=original, _handler=handler, _up=up_ability, _form=burrowed_type):
        burrowed = unit.type_id == _form or bool(getattr(unit, "is_burrowed", False))
        if burrowed and _handler.move_type in RETREAT:
            if _handler.cd_manager.is_ready(unit.tag, _up):
                return cast(_handler, unit, _up)
            return NoAction()
        return _original(unit, command)

    handler.unit_solve_combat = wrapped


class MicroViperSafe(MicroVipers):
    """Consume a legal own Zerg building when energy is low, then use Sharpy spells."""

    def unit_solve_combat(self, unit, command):
        if self._consume_in_progress(unit):
            return NoAction()
        ability = self._consume_ability(unit)
        target = self._consume_target(unit) if ability is not None else None
        if (self.move_type not in RETREAT and float(getattr(unit, "energy", 0) or 0) < CONSUME_ENERGY_LIMIT
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

    def _consume_in_progress(self, unit) -> bool:
        for order in getattr(unit, "orders", []) or []:
            ability = getattr(order, "ability", None)
            ident = getattr(ability, "exact_id", None) or getattr(ability, "id", None)
            if "VIPERCONSUME" in str(ident or "").upper():
                return True
        return False

    def _consume_ability(self, unit):
        # Structure Consume is the building cast. The mineral variant does not drain a building.
        if self.cd_manager.is_ready(unit.tag, _CONSUME_ABILITY):
            return _CONSUME_ABILITY
        return None

    def _consume_target(self, unit):
        if float(getattr(unit, "energy", 0) or 0) >= CONSUME_ENERGY_LIMIT:
            return None
        pool = []
        seen = set()
        for source in (
            getattr(getattr(self, "ai", None), "structures", ()) or (),
            getattr(getattr(self, "ai", None), "units", ()) or (),
        ):
            for ally in source:
                tag = getattr(ally, "tag", None)
                if tag in seen:
                    continue
                seen.add(tag)
                pool.append(ally)
        enemies = list(_enemies(self))
        enemies.extend(getattr(getattr(self, "ai", None), "enemy_units", ()) or ())
        legal = [ally for ally in pool if self._consume_legal(unit, ally, pool, enemies)]
        if not legal:
            return None
        return min(legal, key=lambda ally: (
            self._consume_rank(ally),
            unit.distance_to(ally),
            -float(getattr(ally, "health", 0) or 0),
        ))

    def _consume_legal(self, unit, ally, pool, enemies) -> bool:
        if getattr(ally, "tag", None) == getattr(unit, "tag", None):
            return False
        if not getattr(ally, "is_structure", False):
            return False
        if ally.type_id not in ZERG_CONSUME_STRUCTURES:
            return False
        if not getattr(ally, "is_ready", True) or float(getattr(ally, "build_progress", 1) or 0) < 1:
            return False
        health = float(getattr(ally, "health", 0) or 0)
        if health <= CONSUME_DAMAGE or float(getattr(ally, "health_percentage", 1) or 0) < 0.25:
            return False
        if self._is_morphing(ally) or self._research_almost_done(ally):
            return False
        if ally.type_id in _TOWNHALLS and self._under_attack(ally, enemies):
            return False
        if ally.type_id in _UNIQUE_TECH and sum(1 for other in pool if getattr(other, "type_id", None) == ally.type_id) <= 1:
            return False
        return unit.distance_to(ally) <= CONSUME_RANGE

    def _consume_rank(self, ally) -> int:
        if ally.type_id in _LOW_VALUE:
            return 0
        if ally.type_id in _TOWNHALLS:
            return 3
        return 1

    def _is_morphing(self, ally) -> bool:
        for order in getattr(ally, "orders", []) or []:
            ability = getattr(order, "ability", None)
            ident = getattr(ability, "exact_id", None) or getattr(ability, "id", None)
            if ident in _MORPH_ABILITIES or "MORPH" in str(ident or "").upper():
                return True
        return False

    def _research_almost_done(self, ally) -> bool:
        for order in getattr(ally, "orders", []) or []:
            if float(getattr(order, "progress", 0) or 0) >= 0.85:
                return True
        return False

    def _under_attack(self, ally, enemies) -> bool:
        return any(getattr(enemy, "is_structure", False) is False and ally.distance_to(enemy) <= 8 for enemy in enemies)

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
