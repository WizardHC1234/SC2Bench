from typing import List, Dict, Optional, Union

from sharpy.combat import *
from sharpy.general.extended_power import ExtendedPower
from sharpy.interfaces import ICombatManager
from sharpy.managers.core import UnitCacheManager, PathingManager, ManagerBase
from sharpy.combat import Action, NoAction
from sc2.units import Units

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.ability_id import AbilityId
from sc2.position import Point2, Point3
from sc2.unit import Unit
import numpy as np
from sklearn.cluster import DBSCAN

ignored = {UnitTypeId.MULE, UnitTypeId.LARVA, UnitTypeId.EGG}


class GroupCombatManager(ManagerBase, ICombatManager):
    rules: MicroRules

    def __init__(self):
        super().__init__()
        self.default_rules = MicroRules()
        self.default_rules.load_default_methods()
        self.default_rules.load_default_micro()
        self.enemy_group_distance = 7

    async def start(self, knowledge: "Knowledge"):
        await super().start(knowledge)
        self.cache: UnitCacheManager = self.knowledge.unit_cache
        self.pather: PathingManager = self.knowledge.pathing_manager
        self._tags: List[int] = []
        self.all_enemy_power = ExtendedPower(self.unit_values)

        await self.default_rules.start(knowledge)

    @property
    def tags(self) -> List[int]:
        return self._tags

    @property
    def regroup_threshold(self) -> float:
        """Percentage 0 - 1 on how many of the attacking units should actually be together when attacking"""
        return self.rules.regroup_percentage

    @property
    def own_group_threshold(self) -> float:
        """
        How much distance must be between units to consider them to be in different groups
        """
        return self.rules.own_group_distance

    @property
    def unit_micros(self) -> Dict[UnitTypeId, MicroStep]:
        return self.rules.unit_micros

    @property
    def generic_micro(self) -> MicroStep:
        return self.rules.generic_micro

    async def update(self):
        self.enemy_groups: List[CombatUnits] = self.group_enemy_units()
        self.all_enemy_power.clear()

        for group in self.enemy_groups:  # type: CombatUnits
            self.all_enemy_power.add_units(group.units)

    async def post_update(self):
        pass

    @property
    def debug(self):
        return self._debug and self.knowledge.debug

    def add_unit(self, unit: Unit):
        if unit.type_id in ignored or not unit.is_ready:
            return

        self._tags.append(unit.tag)

    def add_units(self, units: Units):
        for unit in units:
            self.add_unit(unit)

    def get_all_units(self) -> Units:
        units = Units([], self.ai)
        for tag in self._tags:
            unit = self.cache.by_tag(tag)
            if unit and unit.is_ready:
                units.append(unit)
        return units

    def execute(self, target: Point2, move_type=MoveType.Assault, rules: Optional[MicroRules] = None):
        our_units = self.get_all_units()
        if len(our_units) < 1:
            self._tags.clear()
            return

        self.rules = rules if rules else self.default_rules

        self.own_groups: List[CombatUnits] = self.group_own_units(our_units)

        if self.debug:
            fn = lambda group: group.center.distance_to(self.ai.start_location)
            sorted_list = sorted(self.own_groups, key=fn)
            for i in range(0, len(sorted_list)):
                sorted_list[i].debug_index = i

        self.rules.handle_groups_func(self, target, move_type)

        self._tags.clear()

    def faster_group_should_regroup(self, group1: CombatUnits, group2: Optional[CombatUnits]) -> bool:
        if not group2:
            return False
        if group1.average_speed < group2.average_speed + 0.1:
            return False
        # Our group is faster, it's a good idea to regroup
        return True

    def regroup(self, group: CombatUnits, target: Union[Unit, Point2]):
        if isinstance(target, Unit):
            target = self.pather.find_path(group.center, target.position, 1)
        else:
            target = self.pather.find_path(group.center, target, 3)
        self.move_to(group, target, MoveType.Push)

    def move_to(self, group: CombatUnits, target, move_type: MoveType):
        self.action_to(group, target, move_type, False)

    def attack_to(self, group: CombatUnits, target, move_type: MoveType):
        self.action_to(group, target, move_type, True)

    def action_to(self, group: CombatUnits, target, move_type: MoveType, is_attack: bool):
        original_target = target
        if isinstance(target, Point2) and group.ground_units:
            if move_type in {MoveType.DefensiveRetreat, MoveType.PanicRetreat}:
                target = self.pather.find_influence_ground_path(group.center, target, 14)
            else:
                target = self.pather.find_path(group.center, target, 14)

        own_unit_cache: Dict[UnitTypeId, Units] = {}

        for unit in group.units:
            real_type = self.unit_values.real_type(unit.type_id)
            units = own_unit_cache.get(real_type, Units([], self.ai))
            if units.amount == 0:
                own_unit_cache[real_type] = units

            units.append(unit)

        for type_id, type_units in own_unit_cache.items():
            micro: MicroStep = self.unit_micros.get(type_id, self.generic_micro)
            micro.init_group(self.rules, group, type_units, self.enemy_groups, move_type, original_target)
            group_action = micro.group_solve_combat(type_units, Action(target, is_attack))

            for unit in type_units:
                final_action = micro.unit_solve_combat(unit, group_action)
                final_action = self.legal_lurker_action(unit, final_action)
                final_action = self.legal_deployment_action(unit, final_action, original_target)
                final_action = self.legal_attack_action(unit, final_action, original_target, attack_move=is_attack)
                final_action.to_commmand(unit)

                if self.debug:
                    if final_action.debug_comment:
                        status = final_action.debug_comment
                    elif final_action.ability:
                        status = final_action.ability.name
                    elif final_action.is_attack:
                        status = "Attack"
                    else:
                        status = "Move"
                    if final_action.target is not None:
                        if isinstance(final_action.target, Unit):
                            status += f": {final_action.target.type_id.name}"
                        else:
                            status += f": {final_action.target}"

                    status += f" G: {group.debug_index}"
                    status += f"\n{move_type.name}"

                    pos3d: Point3 = unit.position3d
                    pos3d = Point3((pos3d.x, pos3d.y, pos3d.z + 2))
                    self.ai._client.debug_text_world(status, pos3d, size=10)

    def legal_lurker_action(self, unit, action):
        """Lurkers move above ground and fire at unit targets while burrowed."""
        if unit.type_id not in {UnitTypeId.LURKERMP, UnitTypeId.LURKERMPBURROWED}:
            return action
        changing = {AbilityId.BURROWDOWN_LURKER, AbilityId.BURROWUP_LURKER,
                    AbilityId.BURROWDOWN, AbilityId.BURROWUP}
        if any(order.ability.id in changing for order in getattr(unit, 'orders', ())):
            return NoAction()
        if isinstance(action, NoAction) or action.ability is not None:
            return action
        if unit.type_id == UnitTypeId.LURKERMP:
            return Action(action.position, False) if action.is_attack and action.position is not None else action
        if action.is_attack:
            # A position attack is rejected; native acquisition keeps firing.
            return action if hasattr(action.target, 'type_id') else NoAction()
        if action.position is not None and unit.distance_to(action.position) > .5:
            return Action(None, False, AbilityId.BURROWUP_LURKER)
        return NoAction()

    def legal_deployment_action(self, unit, action, objective):
        """Recover a rejected tank deployment without retrying the same footprint."""
        if unit.type_id not in {UnitTypeId.SIEGETANK, UnitTypeId.SIEGETANKSIEGED}:
            return action
        relocations = getattr(self.ai, '_bench_siege_relocations', None)
        if relocations is None:
            relocations = self.ai._bench_siege_relocations = {}
        if unit.type_id == UnitTypeId.SIEGETANKSIEGED:
            relocations.pop(unit.tag, None)
            return action
        if any(getattr(getattr(order, 'ability', None), 'id', None) in {
                AbilityId.SIEGEMODE_SIEGEMODE, AbilityId.UNSIEGE_UNSIEGE}
                for order in getattr(unit, 'orders', ())):
            return action
        # A shot, retreat or a new destination keeps its normal combat priority.
        if action.is_attack or (action.position is not None):
            relocations.pop(unit.tag, None)
            return action
        state = getattr(self.ai, 'state', None)
        failed = any(e.unit_tag == unit.tag and e.ability_id == AbilityId.SIEGEMODE_SIEGEMODE.value
                     and e.result == 208 for e in getattr(state, 'action_errors', ()))
        point = relocations.get(unit.tag)
        if not failed and point is None:
            return action
        radius = float(unit.radius)
        obstacles = [other for other in list(self.ai.all_own_units) + list(self.ai.all_enemy_units)
                     if other.tag != unit.tag and not other.is_flying
                     and not getattr(other, 'is_memory', False)]
        base_center = getattr(getattr(self, 'rules', None), 'reserved_base_center', None)
        def clear(candidate):
            if base_center is not None:
                dx = max(abs(candidate.x-base_center.x)-2.5, 0)
                dy = max(abs(candidate.y-base_center.y)-2.5, 0)
                if dx*dx+dy*dy <= (radius+.25)**2:
                    return False
            return not any(other.position.distance_to(candidate) < radius + float(other.radius) + .15
                           for other in obstacles)
        if point is not None and not clear(point):
            relocations.pop(unit.tag, None)
            point = None
            failed = True
        if failed and point is None:
            import math
            boundary = getattr(getattr(self, 'rules', None), 'boundary', None)
            candidates = []
            for distance in (1.5, 2.5, 3.5, 4.5):
                for angle in range(0, 360, 30):
                    rad = math.radians(angle)
                    candidate = unit.position.offset((math.cos(rad)*distance, math.sin(rad)*distance))
                    footprint = [candidate.offset((dx*radius, dy*radius))
                                 for dx,dy in ((0,0),(1,0),(-1,0),(0,1),(0,-1))]
                    if callable(boundary) and not boundary(candidate):
                        continue
                    if not all(self.ai.in_pathing_grid(p) for p in footprint):
                        continue
                    if not clear(candidate):
                        continue
                    candidates.append(candidate)
                if candidates:
                    break
            if candidates:
                goal = objective.position if isinstance(objective, Unit) else objective
                point = min(candidates, key=lambda p: p.distance_to(goal)) if goal is not None else candidates[0]
                relocations[unit.tag] = point
        if point is not None:
            if unit.distance_to(point) > .5:
                return Action(point, False)
            relocations.pop(unit.tag, None)
        return NoAction() if failed else action

    def legal_attack_action(self, unit, action, objective, *, attack_move=True):
        """Do not fire a ground-only weapon at an air form or rejected transition."""
        target = action.target
        if (action.is_attack and action.ability is None and hasattr(target, 'tag')
                and (unit.type_id.value, target.tag) in getattr(self.ai, '_bench_unit_attack_rejections', set())):
            if not attack_move:
                return Action(objective, False)
            if (getattr(unit, 'is_structure', False) or unit.type_id in {
                    UnitTypeId.SIEGETANKSIEGED, UnitTypeId.LURKERMPBURROWED,
                    UnitTypeId.WIDOWMINEBURROWED, UnitTypeId.LIBERATORAG}):
                return NoAction()
            return Action(target.position, True)
        if (action.is_attack and action.ability is None
                and getattr(target, 'type_id', None) == UnitTypeId.INTERCEPTOR
                and self.unit_values.can_shoot_air(unit)):
            # This client rejects unit-tag attacks on Interceptors (both 23
            # and 3674). Keep native weapon targeting active at their position.
            return Action(target.position if attack_move else objective, attack_move)
        if (not action.is_attack or action.ability is not None
                or not hasattr(target, 'type_id') or self.unit_values.can_shoot_air(unit)):
            return action
        rejected = getattr(self.ai, '_bench_ground_attack_rejections', {})
        if (getattr(target, 'is_flying', False) or target.type_id == UnitTypeId.VIKINGFIGHTER
                or rejected.get(target.tag, 0) > float(getattr(self.ai, 'time', 0))):
            if (getattr(unit, 'is_structure', False) or unit.type_id in {
                    UnitTypeId.SIEGETANKSIEGED, UnitTypeId.LURKERMPBURROWED,
                    UnitTypeId.WIDOWMINEBURROWED, UnitTypeId.LIBERATORAG}):
                return NoAction()
            # Attack-move keeps native ground combat active while avoiding an
            # invalid unit-tag order. The region objective remains unchanged.
            return Action(getattr(objective, 'position', objective) or unit.position, attack_move)
        return action

    def closest_group(
        self,
        start: Point2,
        combat_groups: List[CombatUnits],
        group_center: Optional[Point2] = None,
        distance: float = 50,
    ) -> Optional[CombatUnits]:
        group = None
        best_distance = distance  # doesn't find enemy groups closer than this

        if group_center is None:
            group_center = start

        for combat_group in combat_groups:
            center = combat_group.center

            if center == group_center:
                continue  # it's the same group!

            distance = start.distance_to(center)
            if distance < best_distance:
                best_distance = distance
                group = combat_group

        return group

    def group_own_units(self, units: Units) -> List[CombatUnits]:
        groups: List[Units] = []

        # import time
        # ns_pf = time.perf_counter_ns()

        numpy_vectors: List[np.ndarray] = []
        for unit in units:
            numpy_vectors.append(np.array([unit.position.x, unit.position.y]))

        if numpy_vectors:
            clustering = DBSCAN(eps=self.enemy_group_distance, min_samples=1, algorithm="kd_tree").fit(numpy_vectors)
            # print(clustering.labels_)

            for index in range(0, len(clustering.labels_)):
                unit = units[index]
                if unit.type_id in self.unit_values.combat_ignore:
                    continue

                label = clustering.labels_[index]

                if label >= len(groups):
                    groups.append(Units([unit], self.ai))
                else:
                    groups[label].append(unit)
            # for label in clustering.labels_:

        # ns_pf = time.perf_counter_ns() - ns_pf
        # print(f"Own unit grouping (v2) took {ns_pf / 1000 / 1000} ms. groups: {len(groups)} units: {len(units)}")

        return [CombatUnits(u, self.knowledge) for u in groups]

    def group_enemy_units(self) -> List[CombatUnits]:
        # Historical sightings remain useful to the agent, but must not become
        # a live combat center or bridge otherwise separate visible groups.
        units = [unit for unit in self.ai.all_enemy_units
                 if unit.is_visible and not unit.is_memory and not unit.is_snapshot
                 and not unit.is_hallucination and unit.can_be_attacked
                 and unit.type_id not in self.unit_values.combat_ignore]
        if not units:
            return []
        clustering = DBSCAN(eps=self.enemy_group_distance, min_samples=1).fit(
            [np.array([unit.position.x, unit.position.y]) for unit in units])
        groups = {}
        for label, unit in zip(clustering.labels_, units):
            groups.setdefault(int(label), Units([], self.ai)).append(unit)
        return [CombatUnits(units, self.knowledge) for units in groups.values()]
