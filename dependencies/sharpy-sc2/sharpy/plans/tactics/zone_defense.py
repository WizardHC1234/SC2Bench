from typing import List, Dict, Optional

from sc2.data import Race
from sc2.ids.ability_id import AbilityId
from sharpy.combat import MoveType
from sharpy.managers.core import UnitValue
from sharpy.plans.acts import ActBase
from sc2.ids.unit_typeid import UnitTypeId
from sc2.unit import Unit

from sharpy.knowledges import Knowledge

from sharpy.managers.core.roles import UnitTask
from sharpy.general.extended_power import ExtendedPower
from sc2.units import Units


class PlanZoneDefense(ActBase):
    ZONE_CLEAR_TIMEOUT = 3
    # Kill worker scouts with a tiny local detachment; never yank the main army.
    LOCAL_SCOUT_HUNTERS = 2
    LOCAL_SCOUT_ZONE_SLACK = 1.1
    LOCAL_SCOUT_NEAR_RANGE = 12.0

    def __init__(self):
        super().__init__()
        self.worker_return_distance2 = 10**10

        self.defender_tags: Dict[int, List[int]] = dict()
        self.defender_secondary_tags: Dict[int, List[int]] = dict()
        self.zone_seen_enemy: Dict[int, float] = dict()

    async def start(self, knowledge: Knowledge):
        await super().start(knowledge)
        self.worker_type: UnitTypeId = knowledge.my_worker_type

        for i in range(0, len(self.zone_manager.expansion_zones)):
            self.defender_tags[i] = []
            self.zone_seen_enemy[i] = -10

    @staticmethod
    def combat_enemies(enemies: Units) -> Units:
        """Enemy units that justify pulling the army (excludes worker scouts)."""
        if not enemies or not enemies.exists:
            return enemies
        return enemies.exclude_type(UnitValue.worker_types)

    @staticmethod
    def worker_enemies(enemies: Units) -> Units:
        if not enemies or not enemies.exists:
            return enemies
        return enemies.of_type(UnitValue.worker_types)

    def defense_required(self, enemies: Units) -> bool:
        # Lone / worker-only scouts must not yank the main army off task.
        return self.combat_enemies(enemies).exists

    def _clear_zone_defense(self, zone_defenders_all: Units, zone_defenders: Units, zone_tags: List[int]) -> None:
        for tank in zone_defenders(UnitTypeId.SIEGETANKSIEGED):
            tank(AbilityId.UNSIEGE_UNSIEGE)
        self.roles.clear_tasks(zone_defenders_all)
        zone_defenders.clear()
        zone_tags.clear()

    def _pick_local_scout_hunters(
        self,
        zone: "Zone",
        scout_position,
        prefer_tags: Optional[List[int]] = None,
    ) -> Units:
        """Up to LOCAL_SCOUT_HUNTERS combat units already near the zone/scout."""
        prefer = set(prefer_tags or [])
        max_zone_dist = float(getattr(zone, "radius", 15.0)) * self.LOCAL_SCOUT_ZONE_SLACK
        pool = Units([], self.ai)
        for task in (
            UnitTask.Idle,
            UnitTask.Moving,
            UnitTask.Attacking,
            UnitTask.Fighting,
            UnitTask.Defending,
        ):
            pool.extend(self.roles.units(task))

        candidates: List[Unit] = []
        seen_tags: set[int] = set()
        for unit in pool:
            if unit.tag in seen_tags:
                continue
            if unit.type_id in UnitValue.worker_types:
                continue
            if not self.unit_values.should_attack(unit):
                continue
            if not self.unit_values.can_shoot_ground(unit):
                continue
            near_zone = unit.distance_to(zone.center_location) <= max_zone_dist
            near_scout = unit.distance_to(scout_position) <= self.LOCAL_SCOUT_NEAR_RANGE
            near_gather = False
            gather = getattr(zone, "gather_point", None)
            if gather is not None:
                near_gather = unit.distance_to(gather) <= self.LOCAL_SCOUT_NEAR_RANGE
            if not (near_zone or near_scout or near_gather):
                continue
            seen_tags.add(unit.tag)
            candidates.append(unit)

        # Keep already-assigned hunters stable, then fill by distance to scout.
        preferred = [unit for unit in candidates if unit.tag in prefer]
        others = [unit for unit in candidates if unit.tag not in prefer]
        preferred.sort(key=lambda unit: unit.distance_to(scout_position))
        others.sort(key=lambda unit: unit.distance_to(scout_position))
        chosen = (preferred + others)[: self.LOCAL_SCOUT_HUNTERS]
        return Units(chosen, self.ai)

    async def _clear_worker_scouts_locally(
        self,
        zone: "Zone",
        zone_tags: List[int],
        enemies: Units,
        zone_defenders_all: Units,
        zone_defenders: Units,
    ) -> None:
        """Send 1–2 nearby units at worker scouts; release everyone else."""
        workers = self.worker_enemies(enemies)
        if not workers.exists:
            self._clear_zone_defense(zone_defenders_all, zone_defenders, zone_tags)
            return

        scout = workers.closest_to(getattr(zone, "gather_point", zone.center_location))
        hunters = self._pick_local_scout_hunters(zone, scout.position, zone_tags)
        hunter_tags = {unit.tag for unit in hunters}

        # Drop previous full-army defenders that are not in the tiny hunter set.
        for unit in list(zone_defenders_all):
            if unit.tag in hunter_tags:
                continue
            if unit.type_id == UnitTypeId.SIEGETANKSIEGED:
                unit(AbilityId.UNSIEGE_UNSIEGE)
            self.roles.clear_task(unit)
            if unit.tag in zone_tags:
                zone_tags.remove(unit.tag)

        zone_tags[:] = [tag for tag in zone_tags if tag in hunter_tags]
        if not hunters.exists:
            return

        for unit in hunters:
            self.roles.set_task(UnitTask.Defending, unit)
            self.combat.add_unit(unit)
            if unit.tag not in zone_tags:
                zone_tags.append(unit.tag)

        self.roles.refresh_tags(self.combat.tags)
        self.combat.execute(scout.position, MoveType.Assault)

    async def execute(self) -> bool:
        unit: Unit

        all_defenders = self.roles.all_from_task(UnitTask.Defending)

        for i in range(0, len(self.zone_manager.expansion_zones)):
            zone: "Zone" = self.zone_manager.expansion_zones[i]
            zone_tags = self.defender_tags[i]

            zone_defenders_all = all_defenders.tags_in(zone_tags)
            zone_worker_defenders = zone_defenders_all(self.worker_type)
            zone_defenders = zone_defenders_all.exclude_type(self.worker_type)
            enemies = zone.known_enemy_units
            combat_enemies = self.combat_enemies(enemies)
            worker_only_threat = bool(enemies.exists and not combat_enemies.exists)

            # Let's loop zone starting from our main, which is the one we want to defend the most
            # Check that zone is either in our control or is our start location that has no Nexus
            if zone_defenders.exists or zone.is_ours or zone == self.zone_manager.own_main_zone:
                if not self.defense_required(enemies):
                    if worker_only_threat:
                        # Local peel only: never pull the main army onto a scout.
                        await self._clear_worker_scouts_locally(
                            zone,
                            zone_tags,
                            enemies,
                            zone_defenders_all,
                            zone_defenders,
                        )
                        continue
                    # Delay before removing defenses in case we just lost visibility of the enemies
                    if (
                        zone.last_scouted_center == self.knowledge.ai.time
                        or self.zone_seen_enemy[i] + PlanZoneDefense.ZONE_CLEAR_TIMEOUT < self.ai.time
                    ):
                        self._clear_zone_defense(zone_defenders_all, zone_defenders, zone_tags)
                        continue  # Zone is well under control.
                    if not enemies.exists:
                        self._clear_zone_defense(zone_defenders_all, zone_defenders, zone_tags)
                        continue
                else:
                    self.zone_seen_enemy[i] = self.ai.time

                # Defend in-place. Never use SearchAndDestroy here: that move
                # type keeps hunting enemies far outside the zone and can drag
                # the whole army into the enemy base after a scout or raid flees.
                leash = zone.radius * 1.25
                assaulting_combat = self.combat_enemies(zone.assaulting_enemies)

                if combat_enemies.exists:
                    enemy_center = combat_enemies.closest_to(zone.center_location).position
                elif assaulting_combat.exists:
                    enemy_center = assaulting_combat.closest_to(zone.center_location).position
                else:
                    enemy_center = zone.gather_point

                # Worker-only leftovers: hold gather, do not chase the scout /
                # pull Attacking or Fighting units.
                if worker_only_threat or (
                    not combat_enemies.exists and not assaulting_combat.exists
                ):
                    defense_target = zone.gather_point
                    include_offensive = False
                elif enemy_center.distance_to(zone.center_location) > leash:
                    defense_target = zone.gather_point
                    include_offensive = True
                else:
                    defense_target = enemy_center
                    include_offensive = True

                defense_required = ExtendedPower(self.unit_values)
                if assaulting_combat.exists:
                    defense_required.add_units(assaulting_combat)
                defense_required.multiply(1.5)

                defenders = ExtendedPower(self.unit_values)

                for unit in zone_defenders:
                    self.combat.add_unit(unit)
                    defenders.add_unit(unit)

                # Add units to defenders that are being warped in.
                for unit in self.roles.units(UnitTask.Idle).not_ready:
                    if unit.distance_to(zone.center_location) < zone.radius:
                        # unit is idle in the zone, add to defenders
                        self.combat.add_unit(unit)
                        self.roles.set_task(UnitTask.Defending, unit)
                        zone_tags.append(unit.tag)

                # Scout / worker-only threats never yank the main army.
                if include_offensive and not defenders.is_enough_for(defense_required):
                    needed = ExtendedPower(self.unit_values)
                    needed.add_power(defense_required)
                    needed.substract_power(defenders)
                    for unit in self.roles.get_defenders(needed, zone.center_location):
                        if unit.distance_to(zone.center_location) < zone.radius:
                            # Only count units that are close as defenders
                            defenders.add_unit(unit)

                        self.roles.set_task(UnitTask.Defending, unit)
                        self.combat.add_unit(unit)
                        zone_tags.append(unit.tag)

                if len(enemies) > 1 or (len(enemies) == 1 and enemies[0].type_id not in UnitValue.worker_types):
                    # Pull workers to defend only and only if the enemy isn't one worker scout
                    if defenders.is_enough_for(defense_required):
                        # Workers should return to mining.
                        for unit in zone_worker_defenders:
                            self.roles.clear_task(unit)

                            zone.go_mine(unit)
                            if unit.tag in zone_tags:  # Just in case, should be in zone tags always.
                                zone_tags.remove(unit.tag)
                        # Zone is well under control without worker defense.
                    else:
                        await self.worker_defence(
                            defenders.power, defense_required, defense_target, zone, zone_tags, zone_worker_defenders
                        )

                self.roles.refresh_tags(self.combat.tags)
                self.combat.execute(defense_target, MoveType.Assault)
        return True  # never block

    async def worker_defence(
        self, defenders: float, defense_required, enemy_center, zone: "Zone", zone_tags, zone_worker_defenders
    ):
        ground_enemies: Units = zone.known_enemy_units.not_flying

        # Enemy value on same level and not on ramp
        hostiles_inside = 0
        for unit in ground_enemies:
            if self.ai.get_terrain_height(unit.position) == self.ai.get_terrain_height(zone.center_location):
                hostiles_inside += self.unit_values.defense_value(unit.type_id)

        if self.ai.workers.amount >= self.ai.supply_used - 2:
            # Workers only, defend for everything
            if zone.our_units.filter(lambda u: u.is_structure and u.health_percentage > 0.6):
                # losing a building, defend for everything
                if ground_enemies(UnitTypeId.PHOTONCANNON):
                    # Don't overreact if it's a low ground cannon rush
                    # 2 per proba and 4 per cannon is optimal
                    defense_count_panic = defense_required.power * 0.75
                else:
                    defense_count_panic = defense_required.power * 1.3

                threshold = 8
            else:
                defense_count_panic = hostiles_inside * 1.3
                threshold = 6  # probably a worker fight?
        else:
            defense_count_panic = hostiles_inside * 1.1
            threshold = 16

        if ground_enemies.exists:
            closest = ground_enemies.closest_to(zone.center_location)
            killing_probes = closest.distance_to(zone.center_location) < 6
        else:
            # No ground enemies near workers. There could be eg. a banshee though.
            killing_probes = False

        # Loop currently defending workers
        for unit in zone_worker_defenders:
            if unit.shield + unit.health < threshold and not killing_probes:
                self.roles.clear_task(unit)
                zone.go_mine(unit)
                if unit.tag in zone_tags:  # Just in case, should be in zone tags always.
                    zone_tags.remove(unit.tag)
            else:
                defenders += self.unit_values.defense_value(self.worker_type)
                self.combat.add_unit(unit)

        if self.ai.time > 5 * 60 and not killing_probes and not self.knowledge.enemy_race == Race.Zerg:
            # late game and enemies aren't killing probes, go back to mining!
            return

        if defense_required.power < 1 and not killing_probes:
            return  # Probably a single scout, don't pull workers

        if zone.our_wall() and self.ai.time < 200:
            possible_defender_workers = self.ai.workers
        else:
            possible_defender_workers = zone.our_workers

        if self.knowledge.my_race == Race.Protoss and not killing_probes:
            # This is to protect against sending all units to defend against zealots and others and just die
            defense_count_panic = defense_count_panic * 0.5

        # Get help from other workers
        # type of worker unit doesn't really matter here, add current worker defenders to defender count
        for worker in possible_defender_workers.tags_not_in(zone_tags):
            # Let's use ones with shield left
            if defenders < defense_count_panic and (worker.shield > 3 or killing_probes):
                zone_tags.append(worker.tag)
                self.roles.set_task(UnitTask.Defending, worker)
                defenders += self.unit_values.defense_value(worker.type_id)
                self.combat.add_unit(worker)

    async def debug_actions(self):
        for zone in self.defender_tags:
            tags: List[int] = self.defender_tags.get(zone)
            for tag in tags:
                unit = self.cache.by_tag(tag)
                if unit:
                    text = f"Defending {zone}"
                    self.client.debug_text_world(text, unit.position3d)
