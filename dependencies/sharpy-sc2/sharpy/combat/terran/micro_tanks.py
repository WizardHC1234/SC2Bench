from typing import Optional, Dict

from sharpy.combat import Action, MoveType, GenericMicro, NoAction
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.ability_id import AbilityId
from sc2.position import Point2
from sc2.unit import Unit


class SiegingStatus:
    def __init__(self, tank: Unit):
        self.requested_time = 0
        self.status = 0
        self.requested_status: Optional[AbilityId] = None
        self.delay = (tank.tag % 10) * 0.4

    def clear_order(self):
        self.requested_status = None
        self.requested_time = 0

    def relay_order(
        self,
        tank: Unit,
        order: AbilityId,
        time: float,
        *,
        immediate: bool = False,
    ) -> Optional[Action]:
        if order is None:
            self.clear_order()
            return None

        if self.requested_status == order:
            delay = 0.0 if immediate else self.delay
            if not immediate and order == AbilityId.SIEGEMODE_SIEGEMODE:
                delay = delay * 0.25

            if time > self.requested_time + delay:
                return Action(None, False, self.requested_status)
        else:
            self.requested_status = order
            self.requested_time = time
            if immediate:
                return Action(None, False, self.requested_status)
        return None


class MicroTanks(GenericMicro):
    """Siege Tank micro.

    Siege / unsiege is decided from enemies near *this tank*, not the whole
    combat group's wide ``enemies_near_by`` radius (which grows with army size
    and can leave tanks sieged far from any real fight).
    """

    LOCAL_SIEGE_MIN = 5.0
    LOCAL_SIEGE_MAX = 13.0
    LOCAL_UNSIEGE = 14.0
    MOVE_UNSIEGE = 17.0
    SND_UNSIEGE = 23.0

    def __init__(self):
        super().__init__()
        self.siege_status: Dict[int, SiegingStatus] = {}

    def get_siege_status(self, tank: Unit) -> SiegingStatus:
        status = self.siege_status.get(tank.tag)
        if status is None:
            status = SiegingStatus(tank)
            self.siege_status[tank.tag] = status
        return status

    def _local_ground_enemies(self, unit: Unit):
        nearby = self.enemies_near_by.not_flying.visible
        if not nearby.exists:
            return nearby
        # Per-tank radius so a distant unit near the army center cannot keep
        # every tank sieged (or trigger "Must target unit" attack spam).
        return nearby.closer_than(self.LOCAL_UNSIEGE + 1.0, unit)

    def unit_solve_combat(self, unit: Unit, current_command: Action) -> Action:
        local_enemies = self._local_ground_enemies(unit)
        siege_mode: Optional[AbilityId] = None
        force_immediate_unsiege = False

        if self.move_type in {MoveType.PanicRetreat, MoveType.DefensiveRetreat}:
            if unit.type_id == UnitTypeId.SIEGETANKSIEGED:
                siege_mode = AbilityId.UNSIEGE_UNSIEGE
                force_immediate_unsiege = not local_enemies.exists
        else:
            if local_enemies.exists:
                distance = local_enemies.closest_distance_to(unit)
            else:
                distance = 100.0

            unsiege_threshold = self.MOVE_UNSIEGE
            if self.move_type == MoveType.SearchAndDestroy:
                unsiege_threshold = self.SND_UNSIEGE

            # Only siege when a visible ground enemy is in the tank's own
            # effective siege band — never from empty-field / army-center noise.
            if (
                unit.type_id == UnitTypeId.SIEGETANK
                and local_enemies.exists
                and self.LOCAL_SIEGE_MIN < distance < self.LOCAL_SIEGE_MAX
            ):
                if (
                    len(self.zone_manager.expansion_zones) > 0
                    and unit.distance_to(
                        self.zone_manager.expansion_zones[0].ramp.bottom_center
                    )
                    > 7
                ):
                    siege_mode = AbilityId.SIEGEMODE_SIEGEMODE

            if unit.type_id == UnitTypeId.SIEGETANKSIEGED:
                if not local_enemies.exists:
                    # Empty field: always unsiege so army control can move again.
                    siege_mode = AbilityId.UNSIEGE_UNSIEGE
                    force_immediate_unsiege = True
                elif distance > unsiege_threshold:
                    siege_mode = AbilityId.UNSIEGE_UNSIEGE
                elif self.move_type in {
                    MoveType.Push,
                    MoveType.Assault,
                    MoveType.Harass,
                    MoveType.ReGroup,
                }:
                    # Follow the group when the fight is no longer local.
                    if distance > self.LOCAL_UNSIEGE:
                        siege_mode = AbilityId.UNSIEGE_UNSIEGE

        status = self.get_siege_status(unit)
        order = status.relay_order(
            unit,
            siege_mode,
            self.ai.time,
            immediate=force_immediate_unsiege,
        )
        if order:
            return order

        if unit.type_id == UnitTypeId.SIEGETANKSIEGED:
            # While still sieged, never re-issue attack commands that need a
            # live unit target (causes "Must target unit" spam and stalls).
            if not local_enemies.exists:
                return NoAction()
            if current_command is not None and current_command.is_attack:
                target = current_command.target
                if target is None:
                    return NoAction()
                if isinstance(target, Point2):
                    return current_command
                if isinstance(target, Unit):
                    try:
                        if unit.distance_to(target) > self.LOCAL_UNSIEGE + 2:
                            return NoAction()
                    except Exception:
                        return NoAction()
                    return current_command
                return NoAction()
            return current_command

        return super().unit_solve_combat(unit, current_command)
