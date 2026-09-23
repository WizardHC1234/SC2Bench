"""Missing Terran micro and safe alternate-form handling.

Simple first-edition rules, not player-level spell optimisation. All spell
casts require cached availability; mission boundaries are enforced afterwards.
"""
from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sharpy.combat import Action, GenericMicro, MicroStep, MoveType
from sharpy.combat.action import NoAction
from sharpy.combat.terran import MicroLiberators, MicroVikings, MicroBattleCruisers
from sc2bench_env.backends.sharpy.compat import has_antiarmor_debuff

RETREAT = {MoveType.DefensiveRetreat, MoveType.PanicRetreat}
IMMOBILE_RELEASE = {
    UnitTypeId.SIEGETANKSIEGED: AbilityId.UNSIEGE_UNSIEGE,
    UnitTypeId.WIDOWMINEBURROWED: AbilityId.BURROWUP_WIDOWMINE,
    UnitTypeId.LIBERATORAG: AbilityId.MORPH_LIBERATORAAMODE,
}


def visible_enemy(enemy):
    return (getattr(enemy, "is_visible", True) and not getattr(enemy, "is_snapshot", False)
            and not getattr(enemy, "is_memory", False) and not getattr(enemy, "is_hallucination", False))


def cast(micro, unit, ability, target=None):
    micro.cd_manager.used_ability(unit.tag, ability)
    # This is only a proposed command. It may be rejected by the engine or
    # replaced by the mission guard; observed evidence is collected in Acts.
    return Action(target, False, ability)


class MicroGhost(GenericMicro):
    def unit_solve_combat(self, unit, command):
        if self.move_type in RETREAT:
            return Action(command.position if command.position is not None else self.original_target, False)
        if any("SNIPE" in str(getattr(getattr(order, "ability", None), "id", ""))
               for order in getattr(unit, "orders", [])):
            return NoAction()  # Do not interrupt Steady Targeting with focus-fire.
        enemies = [e for e in self.enemies_near_by if visible_enemy(e)]
        if unit.energy >= 75 and self.cd_manager.is_ready(unit.tag, AbilityId.EMP_EMP):
            targets = [e for e in enemies if e.distance_to(unit) <= 10
                       and (getattr(e, "shield", 0) >= 50 or getattr(e, "energy", 0) >= 50)]
            if targets:
                target = max(targets, key=lambda e: getattr(e, "shield", 0) + getattr(e, "energy", 0))
                return cast(self, unit, AbilityId.EMP_EMP, target.position)
        if self.cd_manager.is_ready(unit.tag, AbilityId.EFFECT_GHOSTSNIPE):
            targets = [e for e in enemies if not e.is_structure and e.is_biological
                       and e.distance_to(unit) <= 10 and e.health >= 100]
            if targets:
                return cast(self, unit, AbilityId.EFFECT_GHOSTSNIPE, max(targets, key=lambda e: e.health))
        if enemies and not unit.is_cloaked and unit.energy >= 50:
            ability = AbilityId.BEHAVIOR_CLOAKON_GHOST
            if self.cd_manager.is_ready(unit.tag, ability) or self.cd_manager.is_ready(unit.tag, AbilityId.BEHAVIOR_CLOAKON):
                return cast(self, unit, ability)
        if unit.is_cloaked and unit.energy <= 15:
            ability = AbilityId.BEHAVIOR_CLOAKOFF_GHOST
            if self.cd_manager.is_ready(unit.tag, ability) or self.cd_manager.is_ready(unit.tag, AbilityId.BEHAVIOR_CLOAKOFF):
                return cast(self, unit, ability)
        return super().unit_solve_combat(unit, command)


class MicroCyclone(GenericMicro):
    def __init__(self):
        super().__init__()
        self.locks = {}
        self.pending_locks = {}

    def unit_solve_combat(self, unit, command):
        if self.move_type in RETREAT:
            self.locks.pop(unit.tag, None)
            self.pending_locks.pop(unit.tag, None)
            return Action(command.position if command.position is not None else self.original_target, False)
        enemies = [e for e in self.enemies_near_by if visible_enemy(e)]
        now = self.ai.time
        # A proposal is not an active lock. Preserve a real acquisition order,
        # and enter sustained kiting only after the target has the Lock On buff.
        acquiring = any("LOCKON" in str(getattr(getattr(order, "ability", None), "id", ""))
                        for order in getattr(unit, "orders", []))
        pending = self.pending_locks.get(unit.tag)
        if pending:
            enemy = next((e for e in enemies if e.tag == pending[0]), None)
            if enemy is not None and enemy.has_buff(BuffId.LOCKON):
                self.locks[unit.tag] = (enemy.tag, now + 14)
                self.pending_locks.pop(unit.tag, None)
            elif not acquiring and now >= pending[1]:
                self.pending_locks.pop(unit.tag, None)
        if acquiring:
            return NoAction()
        lock = self.locks.get(unit.tag)
        if lock:
            enemy = next((e for e in enemies if e.tag == lock[0]), None)
            if (enemy is not None and enemy.has_buff(BuffId.LOCKON)
                    and now < lock[1] and enemy.distance_to(unit) <= 15):
                # Keep firing. A plain move cancels Lock On before it deals damage.
                return Action(enemy, True)
            self.locks.pop(unit.tag, None)
        if unit.tag in self.pending_locks:
            return NoAction()  # Briefly await the engine, never pretend 14s success.
        ground = [e for e in enemies if (not e.is_flying) and e.distance_to(unit) <= 7
                  and not getattr(e, "is_cloaked", False)]
        air = [e for e in enemies if e.is_flying and e.distance_to(unit) <= 7
               and not getattr(e, "is_cloaked", False)]
        # This client has one Lock On ability for ground and air.
        targets = ground or air
        if targets and self.cd_manager.is_ready(unit.tag, AbilityId.LOCKON_LOCKON):
            enemy = max(targets, key=lambda e: e.health)
            self.pending_locks[unit.tag] = (enemy.tag, now + 1)
            return cast(self, unit, AbilityId.LOCKON_LOCKON, enemy)
        return super().unit_solve_combat(unit, command)


class MicroHellionSafe(GenericMicro):
    """Hellbats fight; Hellions run. The morph stays unavailable without an Armory."""

    def unit_solve_combat(self, unit, command):
        hellbat = unit.type_id == UnitTypeId.HELLIONTANK
        enemies = [enemy for enemy in self.enemies_near_by
                   if visible_enemy(enemy) and not enemy.is_flying
                   and not getattr(enemy, "is_structure", False)
                   and enemy.distance_to(unit) <= 6]
        if self.move_type in RETREAT or not enemies:
            if hellbat and self.cd_manager.is_ready(unit.tag, AbilityId.MORPH_HELLION):
                return cast(self, unit, AbilityId.MORPH_HELLION)
            return Action(command.position if command.position is not None else self.original_target, False)
        if not hellbat and self.cd_manager.is_ready(unit.tag, AbilityId.MORPH_HELLBAT):
            return cast(self, unit, AbilityId.MORPH_HELLBAT)
        return super().unit_solve_combat(unit, command)


class MicroThor(GenericMicro):
    def unit_solve_combat(self, unit, command):
        if self.move_type not in RETREAT:
            air = [e for e in self.enemies_near_by if visible_enemy(e) and e.is_flying]
            heavy = any(not getattr(e, "is_light", False) for e in air)
            ability = None
            if heavy and unit.type_id == UnitTypeId.THOR:
                ability = AbilityId.MORPH_THORHIGHIMPACTMODE
            elif air and not heavy and unit.type_id == UnitTypeId.THORAP:
                ability = AbilityId.MORPH_THOREXPLOSIVEMODE
            if ability is not None and self.cd_manager.is_ready(unit.tag, ability):
                return cast(self, unit, ability)
        return super().unit_solve_combat(unit, command)


class MicroWidowMine(GenericMicro):
    def unit_solve_combat(self, unit, command):
        if self.move_type in RETREAT:
            if unit.type_id == UnitTypeId.WIDOWMINEBURROWED:
                return Action(None, False, AbilityId.BURROWUP_WIDOWMINE)
            return Action(command.position if command.position is not None else self.original_target, False)
        enemies = [e for e in self.enemies_near_by if visible_enemy(e)]
        distance = min((e.distance_to(unit) for e in enemies), default=100)
        if unit.type_id == UnitTypeId.WIDOWMINE and distance <= 10:
            if self.cd_manager.is_ready(unit.tag, AbilityId.BURROWDOWN_WIDOWMINE):
                return cast(self, unit, AbilityId.BURROWDOWN_WIDOWMINE)
        if unit.type_id == UnitTypeId.WIDOWMINEBURROWED:
            if distance >= 14:
                return Action(None, False, AbilityId.BURROWUP_WIDOWMINE)
            return NoAction()
        return super().unit_solve_combat(unit, command)


class MicroLiberatorSafe(MicroLiberators):
    def unit_solve_combat(self, unit, command):
        if self.move_type in RETREAT:
            if unit.type_id == UnitTypeId.LIBERATORAG:
                return Action(None, False, AbilityId.MORPH_LIBERATORAAMODE)
            return Action(command.position if command.position is not None else self.original_target, False)
        return super().unit_solve_combat(unit, command)


class MicroVikingSafe(MicroVikings):
    def unit_solve_combat(self, unit, command):
        if self.move_type in RETREAT:
            if unit.type_id == UnitTypeId.VIKINGASSAULT:
                return Action(None, False, AbilityId.MORPH_VIKINGFIGHTERMODE)
            return Action(command.position if command.position is not None else self.original_target, False)
        return super().unit_solve_combat(unit, command)


class MicroBattlecruiserSafe(MicroBattleCruisers):
    def unit_solve_combat(self, unit, command):
        orders = [str(getattr(getattr(order, "ability", None), "id", ""))
                  for order in getattr(unit, "orders", [])]
        if any("TACTICALJUMP" in order for order in orders):
            return NoAction()  # Do not overwrite the real teleport wind-up.
        health = getattr(unit, "health_percentage", 1.0)
        if (health < 0.3
                and self.cd_manager.is_ready(unit.tag, AbilityId.EFFECT_TACTICALJUMP)):
            return cast(self, unit, AbilityId.EFFECT_TACTICALJUMP, self.ai.start_location)
        if self.move_type in RETREAT:
            return Action(command.position if command.position is not None else self.original_target, False)
        if any("YAMATO" in order for order in orders):
            return NoAction()  # Preserve charging; ordinary fire can cancel it.
        return super().unit_solve_combat(unit, command)


class MicroRavenSupport(MicroStep):
    def __init__(self):
        super().__init__()
        self.turret_points = {}
        self.prepared_at = -100
        self.claimed_until = {}

    async def prepare(self, units, ai):
        if ai.time - self.prepared_at < 1:
            return
        self.prepared_at = ai.time
        self.turret_points.clear()
        self.claimed_until = {tag: until for tag, until in self.claimed_until.items() if until > ai.time}
        enemies = [e for e in ai.enemy_units if visible_enemy(e)]
        for unit in units:
            if unit.type_id != UnitTypeId.RAVEN or unit.energy < 50:
                continue
            nearby = [e for e in enemies if e.distance_to(unit) <= 8]
            if nearby:
                enemy = min(nearby, key=lambda e: e.distance_to(unit))
                # A 2x2 turret must be aligned to the building grid. The single
                # unrounded forward point may be invalid or occupied by the
                # target, workers, or a building; try a small local ring.
                center = unit.position.towards(enemy.position, min(2, unit.distance_to(enemy))).rounded
                creation = ai._game_data.units[UnitTypeId.AUTOTURRET.value].creation_ability
                if creation is None:
                    continue
                for offset in ((0, 0), (2, 0), (-2, 0), (0, 2), (0, -2),
                               (2, 2), (-2, 2), (2, -2), (-2, -2)):
                    point = center + Point2(offset)
                    if await ai.can_place_single(creation, point):
                        self.turret_points[unit.tag] = point
                        break

    def unit_solve_combat(self, unit, command):
        if self.move_type in RETREAT:
            return Action(command.position if command.position is not None else self.original_target, False)
        enemies = [e for e in self.enemies_near_by if visible_enemy(e)]
        if unit.energy >= 75 and self.cd_manager.is_ready(unit.tag, AbilityId.EFFECT_INTERFERENCEMATRIX):
            targets = [e for e in enemies if not e.is_structure and e.distance_to(unit) <= 9
                       and (e.is_mechanical or e.is_psionic)
                       and not e.has_buff(BuffId.RAVENSCRAMBLERMISSILE)
                       and self.claimed_until.get(e.tag, 0) <= self.ai.time]
            if targets:
                target = max(targets, key=lambda e: e.health)
                self.claimed_until[target.tag] = self.ai.time + 11
                return cast(self, unit, AbilityId.EFFECT_INTERFERENCEMATRIX, target)
        if unit.energy >= 75 and self.cd_manager.is_ready(unit.tag, AbilityId.EFFECT_ANTIARMORMISSILE):
            version = getattr(getattr(getattr(self, "knowledge", None), "version_manager", None),
                              "full_version", "")
            targets = [e for e in enemies if e.distance_to(unit) <= 10
                       and not has_antiarmor_debuff(e, version)
                       and not e.has_buff(BuffId.RAVENSHREDDERMISSILETINT)
                       and self.claimed_until.get(e.tag, 0) <= self.ai.time
                       and sum(other.distance_to(e) <= 3 for other in enemies) >= 3]
            if targets:
                target = max(targets, key=lambda e: e.health)
                self.claimed_until[target.tag] = self.ai.time + 3
                return cast(self, unit, AbilityId.EFFECT_ANTIARMORMISSILE, target)
        point = self.turret_points.pop(unit.tag, None)
        ability = AbilityId.BUILDAUTOTURRET_AUTOTURRET
        if point is not None and unit.energy >= 50 and self.cd_manager.is_ready(unit.tag, ability):
            return cast(self, unit, ability, point)
        return Action(command.position if command.position is not None else self.original_target, False)
