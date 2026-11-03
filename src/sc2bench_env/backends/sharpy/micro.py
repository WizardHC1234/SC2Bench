"""Platform-owned combat micro extensions used by combat missions.

Sharpy supplies the general combat behavior. These handlers add missing form
control and support behavior while mission boundaries remain platform-owned.
"""
from __future__ import annotations


from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units
from sharpy.combat import Action, GenericMicro, MicroRules, MicroStep
from sharpy.combat.action import NoAction
from sharpy.combat.move_type import MoveType
from sharpy.combat.terran import MicroTanks
from sharpy.combat.zerg import MicroLurkers

from sc2bench_env.backends.sharpy.combat_styles import at_defend_station, DEFEND_DEPLOY_RADIUS, visible_combat_enemies


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
            # Boost away from anti-air. Healing would walk back into that fire.
            threats = [
                enemy for enemy in getattr(self, "enemies_near_by", ()) or ()
                if getattr(enemy, "can_attack_air", False) and enemy.distance_to(unit) <= 10
            ]
            ability = AbilityId.EFFECT_MEDIVACIGNITEAFTERBURNERS
            if threats and self.cd_manager.is_ready(unit.tag, ability):
                return Action(None, False, ability)
            return Action(current_command.position if current_command.position is not None else self.original_target, False)
        healable = self.group.ground_units.filter(self._is_healable)
        if unit.energy >= self.HEAL_ENERGY_FLOOR and healable:
            frame = getattr(getattr(self.ai, "state", None), "game_loop", self.ai.time)
            claims = getattr(self.ai, "_bench_medivac_patients", None)
            if claims is None or claims[0] != frame:
                claims = self.ai._bench_medivac_patients = (frame, {})
            claims = claims[1]
            patients = {patient.tag: patient for patient in healable}
            own_target = self._patient_tag(self._healing_target(unit))
            if own_target in patients:
                claims[unit.tag] = own_target
                return NoAction()
            if claims.get(unit.tag) in patients:
                return NoAction()
            occupied = {target for caster, target in claims.items()
                        if caster != unit.tag and isinstance(target, int)}
            for other in getattr(self.ai, "units", ()):
                if other.type_id == UnitTypeId.MEDIVAC and other.tag != unit.tag:
                    other_target = self._patient_tag(self._healing_target(other))
                    if other_target is not None:
                        occupied.add(other_target)
            available = healable.filter(lambda patient: patient.tag not in occupied)
            if available:
                patient = available.closest_to(unit)
                claims[unit.tag] = patient.tag
                return Action(patient, False, AbilityId.MEDIVACHEAL_HEAL)

        escort = self._escort_point(unit, current_command)
        if self.enemies_near_by:
            escort = self.pather.find_weak_influence_air(escort, 8)
        return Action(escort, False)

    @staticmethod
    def _healing_target(unit):
        return next((order.target for order in getattr(unit, "orders", ())
                     if "HEAL" in str(getattr(getattr(order, "ability", None), "id", "")).upper()), None)

    @staticmethod
    def _patient_tag(target):
        """Heal orders may target a unit tag or a map point. Only a tag is a patient."""
        if type(target) is int:
            return target
        tag = getattr(target, "tag", None)
        if type(tag) is int:
            return tag
        return None

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


class MicroDefendTanks(MicroTanks):
    """Deploy at a reached defend station and remain deployed there."""

    def unit_solve_combat(self, unit: Unit, current_command: Action) -> Action:
        if _tank_transform_pending(unit):
            return NoAction()
        local = self._local_ground_enemies(unit)
        if (unit.type_id == UnitTypeId.SIEGETANK and local.exists
                and local.closest_distance_to(unit) <= self.LOCAL_SIEGE_MIN):
            return super().unit_solve_combat(unit, current_command)
        if at_defend_station(self, unit):
            status = self.get_siege_status(unit)
            if unit.type_id == UnitTypeId.SIEGETANK:
                order = status.relay_order(
                    unit, AbilityId.SIEGEMODE_SIEGEMODE, self.ai.time, immediate=True,
                )
                return order or NoAction()
            if unit.type_id == UnitTypeId.SIEGETANKSIEGED:
                status.relay_order(unit, None, self.ai.time)
                return NoAction()
        rules = getattr(self,"rules",None)
        station = getattr(rules,"return_point",None)
        if (getattr(rules,"station_before_deploy",False) and station is not None
                and self.move_type not in {MoveType.DefensiveRetreat,MoveType.PanicRetreat}):
            if local.exists:
                distance = local.closest_distance_to(unit)
                if unit.type_id == UnitTypeId.SIEGETANKSIEGED and distance <= self.LOCAL_UNSIEGE:
                    self.get_siege_status(unit).relay_order(unit,None,self.ai.time)
                    return NoAction()
                if unit.type_id == UnitTypeId.SIEGETANK and self.LOCAL_SIEGE_MIN < distance < self.LOCAL_SIEGE_MAX:
                    return self.get_siege_status(unit).relay_order(
                        unit,AbilityId.SIEGEMODE_SIEGEMODE,self.ai.time,immediate=True) or NoAction()
                if distance <= self.LOCAL_SIEGE_MIN:
                    return super().unit_solve_combat(unit,current_command)
                # Contact outside the siege band still belongs to combat micro,
                # rather than a move back to the station.
                return super().unit_solve_combat(unit,current_command)
            if unit.type_id == UnitTypeId.SIEGETANKSIEGED:
                return Action(None,False,AbilityId.UNSIEGE_UNSIEGE)
            return Action(station,False)
        return super().unit_solve_combat(unit, current_command)


class MicroDefendLurkers(MicroLurkers):
    """Burrow at a reached defend station and remain able to fire there."""

    def unit_solve_combat(self, unit: Unit, current_command: Action) -> Action:
        if at_defend_station(self, unit):
            status = self.get_siege_status(unit)
            if unit.type_id == UnitTypeId.LURKERMP:
                order = status.relay_order(
                    unit, AbilityId.BURROWDOWN_LURKER, self.ai.time,
                )
                return order or NoAction()
            if unit.type_id == UnitTypeId.LURKERMPBURROWED:
                status.relay_order(unit, None, self.ai.time)
                return NoAction()
        return super().unit_solve_combat(unit, current_command)


class MissionMicroRules(MicroRules):
    """Apply mission boundaries AFTER specialized micro chooses its command.

    Changing only the group destination does not stop focus-fire chasing.
    Rules are per mission, so the guard never changes global Sharpy defaults.
    """

    def __init__(self):
        super().__init__()
        self.boundary = None
        self.return_point = None
        self.hold_position = False
        self.station_before_deploy = False
        self.reserved_base_center = None
        self._builder_yields = {}
        self.compact_station = False
        self._station_origin = None
        self._settled_at = {}

    @staticmethod
    def _stationary(unit):
        if unit.type_id in {UnitTypeId.SIEGETANKSIEGED, UnitTypeId.LURKERMPBURROWED,
                            UnitTypeId.WIDOWMINEBURROWED, UnitTypeId.LIBERATORAG}:
            return True
        return all(str(getattr(getattr(getattr(o, 'ability', None), 'id', None), 'name', '')).
                   startswith(('HOLDPOSITION', 'STOP')) for o in getattr(unit, 'orders', ()) or ())

    def at_station(self, unit, *, group_units=()):
        point = self.return_point
        if point is None:
            return False
        distance = unit.distance_to(point)
        if not self.compact_station or getattr(unit, 'is_flying', False):
            return distance <= DEFEND_DEPLOY_RADIUS
        if self._station_origin is None or self._station_origin.distance_to(point) > .5:
            self._settled_at.clear()
            self._station_origin = point
        previous = self._settled_at.get(unit.tag)
        if previous is not None and unit.distance_to(previous) <= .5:
            return True
        self._settled_at.pop(unit.tag, None)
        radius = float(getattr(unit, 'radius', .5))
        arrived = distance <= max(1.0, radius)
        # Keep already deployed defenders stable; incoming mobile units pack
        # toward the center before deploying.
        if self._stationary(unit) and unit.type_id in {
                UnitTypeId.SIEGETANKSIEGED, UnitTypeId.LURKERMPBURROWED,
                UnitTypeId.WIDOWMINEBURROWED, UnitTypeId.LIBERATORAG}:
            arrived = distance <= DEFEND_DEPLOY_RADIUS
        if not arrived:
            for ally in group_units:
                parked = self._settled_at.get(ally.tag)
                if (ally.tag == unit.tag or parked is None or getattr(ally, 'is_flying', False)
                        or ally.distance_to(parked) > .5 or not self._stationary(ally)
                        or ally.distance_to(point) >= distance - .25):
                    continue
                if unit.distance_to(ally) <= radius + float(getattr(ally, 'radius', .5)) + .3:
                    arrived = True
                    break
        if arrived:
            self._settled_at[unit.tag] = unit.position
        return arrived

    async def start(self, knowledge):
        await super().start(knowledge)
        seen = set()
        for micro in [self.generic_micro, *self.unit_micros.values()]:
            if id(micro) in seen:
                continue
            seen.add(id(micro))
            solve = micro.unit_solve_combat

            def guarded(unit, command, solve=solve, micro=micro):
                return self.guard_action(unit, solve(unit, command),
                                         group_units=getattr(getattr(micro, "group", None), "units", ()))

            micro.unit_solve_combat = guarded

    def guard_action(self, unit, command, *, group_units=()):
        if _form_change_pending(unit):
            return NoAction()
        if self.boundary is None or self.return_point is None:
            return command
        # Local combat can cross the station edge. Sight alone must not keep
        # pulling a defending group after a fleeing enemy across the map.
        enemies = visible_combat_enemies(getattr(self, "ai", None), unit, group_units,
                                        unit_values=getattr(self,"unit_values",None))
        if enemies and (
                not self.hold_position or self.boundary(unit.position)
                or any(self.boundary(enemy.position) for enemy in enemies)
                or self._weapon_contact(unit, enemies)):
            return command
        if self.hold_position and not getattr(unit, 'is_flying', False):
            yield_point = self._builder_passage(unit)
            if yield_point is not None:
                return self._return_to_station(unit, yield_point) if unit.distance_to(yield_point) > .35 else NoAction()
        center = self.reserved_base_center
        if self.hold_position and center is not None and not getattr(unit, 'is_flying', False):
            from sc2bench_env.backends.sharpy.holding import overlaps_townhall_site
            if overlaps_townhall_site(unit.position, center, float(getattr(unit, 'radius', .75))):
                return self._return_to_station(unit)
        # A spell already in range is not a chase. Defend used to replace EMP,
        # Lock On, Matrix and Yamato with a move back to the hold point.
        if self._keep_local_cast(unit, command):
            return command
        if self.hold_position:
            # An in-range shot does not move across the boundary, even when
            # the enemy is on the far side of a ramp or defense leash.
            target = getattr(command,"target",None)
            if (command.is_attack and target is not None and hasattr(target,"type_id")
                    and not bool(getattr(target,"is_structure",False))):
                try:
                    attack_range=float(self.unit_values.real_range(unit,target))
                    if attack_range>0 and unit.distance_to(target)<=attack_range:
                        return command
                except (AttributeError,TypeError,ValueError):
                    pass
            if isinstance(command,NoAction) and self.boundary(unit.position):
                if (not self.compact_station or getattr(unit, 'is_flying', False)
                        or (self.at_station(unit, group_units=group_units) and self._stationary(unit))):
                    return command
        outside = not self.boundary(unit.position)
        target_outside = command.position is not None and not self.boundary(command.position)
        if outside or target_outside:
            return self._return_to_station(unit)
        if self.hold_position:
            if self.at_station(unit, group_units=group_units):
                if any(getattr(getattr(order,"ability",None),"id",None) in {
                           AbilityId.HOLDPOSITION_HOLD,AbilityId.HOLDPOSITION,AbilityId.HOLDPOSITION_BATTLECRUISER}
                       for order in getattr(unit,"orders",()) or ()):
                    return NoAction()
                return Action(None,False,AbilityId.HOLDPOSITION_HOLD)
            return Action(self.return_point, False)
        return command

    def _weapon_contact(self, unit, enemies):
        """Keep firing and evasive micro when either side can already shoot."""
        from sc2bench_env.backends.sharpy.weapon_facts import has_active_weapon

        values = getattr(self, "unit_values", None)
        for enemy in enemies:
            for attacker, target in ((unit, enemy), (enemy, unit)):
                if not has_active_weapon(attacker):
                    continue
                if values is not None:
                    reach = float(values.real_range(attacker, target))
                    if reach > 0 and attacker.distance_to(target) <= reach:
                        return True
                elif callable(getattr(attacker, "target_in_range", None)) and attacker.target_in_range(target):
                    return True
        return False

    def _builder_passage(self, unit):
        """Let a builder or an explicitly casting Queen pass resting defenders."""
        ai = getattr(self, 'ai', None)
        workers = {w.tag:w for w in getattr(ai, 'workers', ())}
        queens = {u.tag:u for u in getattr(ai, 'units', ()) if u.type_id == UnitTypeId.QUEEN}
        builders = {}
        for task in getattr(ai, '_macro_tasks', ()):
            act = task.get('_act')
            if task.get('action') in {'inject_larva', 'spawn_creep_tumor'}:
                pending = getattr(act, '_pending_cast', None)
                queen = queens.get(pending.get('caster')) if pending else None
                point = pending.get('point') if pending else None
                if queen is not None and point is not None:
                    builders[queen.tag] = (queen, point)
            if task.get('action') != 'build':
                continue
            worker = workers.get(getattr(act, 'builder_tag', None))
            order = getattr(act, '_last_build_order', None)
            if worker is not None and order and not getattr(worker, 'is_constructing_scv', False):
                builders[worker.tag] = (worker, order['point'])
        if not builders:
            self._builder_yields.clear()
            return None
        ground = {other.tag: other for other in
                  [*getattr(ai, 'units', ()), *getattr(ai, 'structures', ())]
                  if not getattr(other, 'is_flying', False)}
        for tag, (actor, _) in list(self._builder_yields.items()):
            if actor not in builders or tag not in ground:
                self._builder_yields.pop(tag, None)
        held = self._builder_yields.get(unit.tag)
        if held is not None:
            worker = builders.get(held[0])
            if worker is not None:
                actor, goal = worker
                if actor.type_id == UnitTypeId.QUEEN:
                    dx, dy = goal.x-actor.position.x, goal.y-actor.position.y
                    length = (dx*dx+dy*dy)**.5
                    radius = float(getattr(unit, 'radius', .5)) + float(getattr(actor, 'radius', .875))
                    # Clear the next rows before the caster reaches them, then
                    # keep them aside until she has passed, rather than letting
                    # them return to the rally point while she is approaching.
                    if length >= 3 and (-radius-.5 < (
                            dx*(unit.position.x-actor.position.x)
                            + dy*(unit.position.y-actor.position.y))/length < 8+radius):
                        return held[1]
                elif unit.distance_to(actor) < 3:
                    return held[1]
            self._builder_yields.pop(unit.tag, None)
        for worker, goal in builders.values():
            if worker.tag == unit.tag:
                continue
            radius = float(getattr(unit, 'radius', .5)) + float(getattr(worker, 'radius', .375))
            queen = worker.type_id == UnitTypeId.QUEEN
            if not queen and unit.distance_to(worker) > radius + 1:
                continue
            dx, dy = goal.x-worker.position.x, goal.y-worker.position.y
            length = (dx*dx+dy*dy)**.5
            if length < 3:
                continue
            dx, dy = dx/length, dy/length
            ux, uy = unit.position.x-worker.position.x, unit.position.y-worker.position.y
            lateral = -dy*ux+dx*uy
            ahead = dx*ux+dy*uy
            limit = min(length, 8) if queen else radius+1
            if not (-.5 < ahead < limit) or abs(lateral) > radius+.2:
                continue
            side = 1 if lateral >= 0 else -1
            for direction in (side, -side):
                for distance in ((1.5, 2.5, 3.5, 4.5, 5.5, 6.5) if queen else (1.5, 2.5, 3.5, 4.5)):
                    if queen and direction*lateral+distance < radius+.3:
                        continue
                    point = unit.position.offset((-dy*direction*distance, dx*direction*distance))
                    pathable = getattr(ai, 'in_pathing_grid', None)
                    occupied = any(other.tag != unit.tag
                        and other.position.distance_to(point) < float(getattr(other, 'radius', .5)) + float(getattr(unit, 'radius', .5)) + .1
                        for other in ground.values())
                    # Multiple defenders can be processed against the same
                    # frozen positions. Their destinations must stay distinct.
                    occupied = occupied or any(tag != unit.tag and point.distance_to(destination)
                        < float(getattr(unit, 'radius', .5)) + float(getattr(other, 'radius', .5)) + .1
                        for tag, (_, destination) in self._builder_yields.items()
                        for other in [ground[tag]])
                    if self.boundary(point) and (not callable(pathable) or pathable(point)) and not occupied:
                        self._builder_yields[unit.tag] = (worker.tag, point)
                        return point
        return None

    def _return_to_station(self, unit, point=None):
        from sc2bench_env.backends.sharpy.protoss_micro import IMMOBILE_RELEASE as protoss_release
        from sc2bench_env.backends.sharpy.terran_micro import IMMOBILE_RELEASE as terran_release
        from sc2bench_env.backends.sharpy.zerg_micro import IMMOBILE_RELEASE as zerg_release
        release = terran_release.get(unit.type_id, protoss_release.get(unit.type_id, zerg_release.get(unit.type_id)))
        return Action(None, False, release) if release is not None else Action(self.return_point if point is None else point, False)

    def _local_enemy_present(self,unit):
        return bool(visible_combat_enemies(getattr(self,"ai",None),unit,
                                          unit_values=getattr(self,"unit_values",None)))

    def _keep_local_cast(self, unit, command) -> bool:
        ability = getattr(command, "ability", None)
        if ability is None:
            return False
        if "TACTICALJUMP" in str(ability).upper():
            return True
        position = command.position
        if position is None:
            return bool(self.boundary(unit.position)) or (self.hold_position and self._local_enemy_present(unit))
        try:
            return float(unit.distance_to(position)) <= 12
        except (AttributeError, TypeError, ValueError):
            return False


def _tank_transform_pending(unit):
    if unit.type_id not in {UnitTypeId.SIEGETANK,UnitTypeId.SIEGETANKSIEGED}:
        return False
    return any(getattr(getattr(order,"ability",None),"id",None) in {
        AbilityId.SIEGEMODE_SIEGEMODE,AbilityId.UNSIEGE_UNSIEGE,
    } for order in getattr(unit,"orders",()) or ())


def _form_change_pending(unit):
    if _tank_transform_pending(unit):
        return True
    changes = {
        UnitTypeId.OBSERVER: {
            AbilityId.MORPH_OBSERVERMODE, AbilityId.MORPH_SURVEILLANCEMODE,
        },
        UnitTypeId.VIKINGFIGHTER: {
            AbilityId.MORPH_VIKINGASSAULTMODE, AbilityId.MORPH_VIKINGFIGHTERMODE,
        },
        UnitTypeId.HELLION: {AbilityId.MORPH_HELLBAT, AbilityId.MORPH_HELLION},
        UnitTypeId.THOR: {AbilityId.MORPH_THORHIGHIMPACTMODE, AbilityId.MORPH_THOREXPLOSIVEMODE},
        UnitTypeId.LURKERMP: {AbilityId.BURROWDOWN_LURKER, AbilityId.BURROWUP_LURKER,
                             AbilityId.BURROWDOWN, AbilityId.BURROWUP},
    }
    changes[UnitTypeId.OBSERVERSIEGEMODE] = changes[UnitTypeId.OBSERVER]
    changes[UnitTypeId.VIKINGASSAULT] = changes[UnitTypeId.VIKINGFIGHTER]
    changes[UnitTypeId.HELLIONTANK] = changes[UnitTypeId.HELLION]
    changes[UnitTypeId.THORAP] = changes[UnitTypeId.THOR]
    changes[UnitTypeId.LURKERMPBURROWED] = changes[UnitTypeId.LURKERMP]
    abilities = changes.get(unit.type_id, ())
    return any(getattr(getattr(order, 'ability', None), 'id', None) in abilities
               for order in getattr(unit, 'orders', ()) or ())


def build_combat_micro_rules() -> MicroRules:
    """Sharpy micro plus platform handlers for the supported races."""
    rules = MissionMicroRules()
    rules.load_default_methods()
    rules.load_default_micro()
    tanks = MicroDefendTanks()
    rules.unit_micros[UnitTypeId.SIEGETANK] = tanks
    rules.unit_micros[UnitTypeId.SIEGETANKSIEGED] = tanks
    rules.unit_micros[UnitTypeId.BANSHEE] = MicroBanshee()
    rules.unit_micros[UnitTypeId.MEDIVAC] = MicroMedivacsSupport()
    from sc2bench_env.backends.sharpy.terran_micro import (
        MicroGhost, MicroCyclone, MicroHellionSafe, MicroThor, MicroWidowMine, MicroLiberatorSafe,
        MicroVikingSafe, MicroRavenSupport, MicroBattlecruiserSafe,
    )
    for types, micro in (
        ((UnitTypeId.GHOST,), MicroGhost()), ((UnitTypeId.CYCLONE,), MicroCyclone()),
        ((UnitTypeId.HELLION, UnitTypeId.HELLIONTANK), MicroHellionSafe()),
        ((UnitTypeId.THOR, UnitTypeId.THORAP), MicroThor()),
        ((UnitTypeId.WIDOWMINE, UnitTypeId.WIDOWMINEBURROWED), MicroWidowMine()),
        ((UnitTypeId.LIBERATOR, UnitTypeId.LIBERATORAG), MicroLiberatorSafe()),
        ((UnitTypeId.VIKINGFIGHTER, UnitTypeId.VIKINGASSAULT), MicroVikingSafe()),
        ((UnitTypeId.RAVEN,), MicroRavenSupport()),
        ((UnitTypeId.BATTLECRUISER,), MicroBattlecruiserSafe()),
    ):
        for unit_type in types:
            rules.unit_micros[unit_type] = micro
    from sc2bench_env.backends.sharpy.protoss_micro import (
        MicroCarrierSafe, MicroDarkTemplarSafe, MicroMothership, MicroObserverSafe,
        MicroOracleSafe, MicroWarpPrismSafe,
    )
    observer = MicroObserverSafe()
    prism = MicroWarpPrismSafe()
    rules.unit_micros[UnitTypeId.OBSERVER] = observer
    rules.unit_micros[UnitTypeId.OBSERVERSIEGEMODE] = observer
    rules.unit_micros[UnitTypeId.WARPPRISM] = prism
    rules.unit_micros[UnitTypeId.WARPPRISMPHASING] = prism
    rules.unit_micros[UnitTypeId.ORACLE] = MicroOracleSafe()
    rules.unit_micros[UnitTypeId.DARKTEMPLAR] = MicroDarkTemplarSafe()
    rules.unit_micros[UnitTypeId.CARRIER] = MicroCarrierSafe()
    rules.unit_micros[UnitTypeId.MOTHERSHIP] = MicroMothership()
    from sc2bench_env.backends.sharpy.zerg_micro import (
        MicroBanelingSafe, MicroEscapeBurrow, MicroOverseerSafe, MicroRoachSafe, MicroViperSafe,
        guard_retreat_unburrow,
    )
    roach = MicroRoachSafe()
    baneling = MicroBanelingSafe()
    rules.unit_micros[UnitTypeId.ROACH] = roach
    rules.unit_micros[UnitTypeId.ROACHBURROWED] = roach
    rules.unit_micros[UnitTypeId.BANELING] = baneling
    rules.unit_micros[UnitTypeId.BANELINGBURROWED] = baneling
    rules.unit_micros[UnitTypeId.VIPER] = MicroViperSafe()
    overseer = MicroOverseerSafe()
    rules.unit_micros[UnitTypeId.OVERSEER] = overseer
    rules.unit_micros[UnitTypeId.OVERSEERSIEGEMODE] = overseer
    lurkers = MicroDefendLurkers()
    rules.unit_micros[UnitTypeId.LURKERMP] = lurkers
    rules.unit_micros[UnitTypeId.LURKERMPBURROWED] = lurkers
    for normal, burrowed, down, up in (
        (UnitTypeId.ZERGLING, UnitTypeId.ZERGLINGBURROWED, AbilityId.BURROWDOWN_ZERGLING, AbilityId.BURROWUP_ZERGLING),
        (UnitTypeId.HYDRALISK, UnitTypeId.HYDRALISKBURROWED, AbilityId.BURROWDOWN_HYDRALISK, AbilityId.BURROWUP_HYDRALISK),
        (UnitTypeId.ULTRALISK, UnitTypeId.ULTRALISKBURROWED, AbilityId.BURROWDOWN_ULTRALISK, AbilityId.BURROWUP_ULTRALISK),
    ):
        handler = MicroEscapeBurrow()
        handler.burrowed_type = burrowed
        handler.down_ability = down
        handler.up_ability = up
        rules.unit_micros[normal] = handler
        rules.unit_micros[burrowed] = handler
    for source, forms in (
        (UnitTypeId.LURKERMP, (UnitTypeId.LURKERMPBURROWED,)),
        (UnitTypeId.INFESTOR, (UnitTypeId.INFESTORBURROWED,)),
        (UnitTypeId.RAVAGER, (UnitTypeId.RAVAGERBURROWED,)),
        (UnitTypeId.QUEEN, (UnitTypeId.QUEENBURROWED,)),
        (UnitTypeId.SWARMHOSTMP, (UnitTypeId.SWARMHOSTBURROWEDMP,)),
    ):
        handler = rules.unit_micros.get(source)
        if handler is None:
            continue
        for form in forms:
            rules.unit_micros[form] = handler
    for form, up in (
        (UnitTypeId.LURKERMPBURROWED, AbilityId.BURROWUP_LURKER),
        (UnitTypeId.INFESTORBURROWED, AbilityId.BURROWUP_INFESTOR),
        (UnitTypeId.RAVAGERBURROWED, AbilityId.BURROWUP_RAVAGER),
        (UnitTypeId.QUEENBURROWED, AbilityId.BURROWUP_QUEEN),
        (UnitTypeId.SWARMHOSTBURROWEDMP, AbilityId.BURROWUP_SWARMHOST),
    ):
        handler = rules.unit_micros.get(form)
        if handler is not None:
            guard_retreat_unburrow(handler, up, form)
    return rules
