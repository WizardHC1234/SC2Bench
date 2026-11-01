"""Race adapter public contract."""
from __future__ import annotations


from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple

from sc2bench_env.runtime.task import Demand, DemandState


def owned_units(ai: Any, unit_type: Any) -> list:
    """Living units of one native type, including cargo, deduplicated by tag."""
    unit_collection = getattr(ai, "units", ())
    on_map = list(unit_collection) if hasattr(unit_collection, "__iter__") else list(unit_collection(unit_type))
    structures = getattr(ai, "structures", ())
    carriers = on_map + (list(structures) if hasattr(structures, "__iter__") else [])
    candidates = list(on_map)
    for carrier in carriers:
        candidates.extend(getattr(carrier, "passengers", ()) or ())
    found = {}
    for unit in candidates:
        if getattr(unit, "type_id", None) == unit_type:
            found[getattr(unit, "tag", id(unit))] = unit
    return list(found.values())


def owned_unit_count(ai: Any, unit_type: Any) -> int:
    return len(owned_units(ai, unit_type))


def research_host_blocker(ai: Any, task: dict, *, race: str) -> Optional[str]:
    """Use the started Tech act's actual compatible hosts before budgeting."""
    if task.get('action') != 'research':
        return None
    act = task.get('_act')
    types = getattr(act, 'from_buildings', None)
    if not isinstance(types, (set, frozenset)) or not types:
        return None
    structures = list(ai.structures)
    parents = [s for s in structures if s.type_id in types and getattr(s, 'is_ready', False)]
    if not parents:
        return None  # Catalog checks identify missing technology.
    parents = [s for s in parents if not getattr(s, 'is_flying', False)]
    if not parents:
        return 'producer_unavailable'
    if race == 'terran' and any(s.type_id.name.endswith('TECHLAB') for s in parents):
        attached = {getattr(s, 'add_on_tag', 0) for s in structures
                    if getattr(s, 'is_ready', False) and not getattr(s, 'is_flying', False)}
        parents = [s for s in parents if s.tag in attached]
        if not parents:
            return 'producer_techlab_unavailable'
    if race == 'protoss':
        parents = [s for s in parents if getattr(s, 'is_powered', True)]
        if not parents:
            return 'unpowered'
    if all(getattr(s, 'orders', None) is not None and len(s.orders) > 0 for s in parents):
        return 'producer_busy'
    return None


class RaceAdapter(ABC):
    """Maps platform targets onto Sharpy Acts."""

    race_name: str = "unknown"
    townhall_targets: Optional[Tuple[str, ...]] = None

    def production_owned_count(self, snapshot: Any, action: str, target: str) -> int:
        """Observed count used only to convert extra demand to an Act target.

        Expansion targets count physical bases, including upgraded townhalls.
        Other targets use the canonical target's raw facts.
        It does not credit output, count queued production or complete tasks.
        """
        townhalls = getattr(self, "townhall_targets", None)
        if action == "build" and townhalls and target == townhalls[0]:
            base_count = snapshot.info.get("base_count")
            if base_count is not None:
                return int(base_count)
            # Upgrade progress describes an existing building, not a new base.
            return (snapshot.owned_count(action, target)
                    + sum(snapshot.buildings.get(name, 0) for name in townhalls[1:]))
        return snapshot.owned_count(action, target)

    def ability_task_state(self, action: str, snapshot: Any,
                           waiting_for: Optional[str]) -> Tuple[DemandState, Optional[str]]:
        """Describe an uncompleted ability task from facts and execution reason.

        Completion/failure remain shared backend facts. Aggregate readiness is
        not an assigned caster, successful cast or command to issue a spell.
        """
        raise NotImplementedError("race adapter must implement ability task feedback")

    def prerequisite_count_keys(self, prerequisite: str) -> Tuple[str, ...]:
        """Ready-count keys satisfying one catalog prerequisite."""
        return (prerequisite,)

    def ability_energy_budget(self, ai: Any, *, available_only: bool = False) -> float:
        """Read this race's ability budget without pooling incompatible casters.

        available_only excludes casters already used during the current frame.
        It must not reset earlier shared soft reservations or issue commands.
        """
        raise NotImplementedError("race adapter must implement ability energy budget")

    def execution_blocker(self, ai: Any, task: Dict[str, Any]) -> Optional[str]:
        """Probe observed host/slot constraints; do not issue or reorder work."""
        raise NotImplementedError("race adapter must implement execution constraints")

    def resource_committed(self, ai: Any, task: Dict[str, Any]) -> bool:
        """Reconcile pending work and private paid markers for budget handling.

        Implementations may clear stale _paid/_resource_committed markers.
        This is not task completion, public queue ownership or cancellation.
        """
        raise NotImplementedError("race adapter must implement payment reconciliation")

    def ready_progress_target(self, task: Dict[str, Any]) -> Optional[str]:
        """Return the ready-count key used by this task's internal budget probe."""
        raise NotImplementedError("race adapter must implement ready-progress mapping")

    def read_ability_facts(self, ai: Any, buildings: Dict[str, int]) -> Dict[str, Any]:
        """Return observed race-specific ability facts, without issuing orders.

        The default publishes no race-specific fields. Do not fill unsupported
        abilities with Terran zeroes or choose when/how energy should be spent.
        """
        return {}

    def count_ready(self, ai: Any, platform_name: str) -> int:
        """Count ready targets for production prerequisites in this race.

        Unlike an unknown observation, missing execution support must fail
        explicitly rather than masquerade as zero owned structures/units.
        """
        raise NotImplementedError("race adapter must implement ready-target counting")

    def worker_build_target(self, worker_name: str, order: Any) -> Optional[str]:
        """Decode an observed worker construction order; unknown returns None."""
        return None

    def production_order_target(self, order: Any, game_data: Any) -> Optional[Tuple[str, str]]:
        """Decode one observed facility order as (action, canonical target).

        Actions are train/build/research. This does not count produced entities,
        infer task ownership, change queues or mark an accepted task complete.
        Unimplemented/unrecognized decoding returns None, without race fallback.
        """
        return None

    def unit_production_counts(self, unit: Any, game_data: Any) -> Dict[str, int]:
        """Native unfinished unit entities, excluding completed inventory."""
        if float(getattr(unit, "build_progress", 1)) >= 1:
            return {}
        from sc2bench_env.catalog.registry import get_target
        name = self.normalize_unit_name(unit.type_id.name)
        spec = get_target(name, race=self.race_name) if name is not None else None
        return {name: 1} if spec is not None and spec.action == "train" else {}

    def read_production_capacity(self, ai: Any) -> Optional[list]:
        """Read observed queue headroom, or None when not implemented.

        This is an observation hook, not a scheduler: do not reserve resources,
        issue commands or infer affordable starts from available queue slots.
        Race-specific fields and unknown values remain the adapter's facts.
        """
        return None

    @abstractmethod
    def create_act(self, task: Demand, *, to_count: int) -> Any:
        """Return a Sharpy ActBase for the task."""

    @abstractmethod
    def normalize_unit_name(self, type_name: str) -> Optional[str]:
        """Map SC2/Sharpy type names to platform target keys."""

    def home_production_types(self) -> frozenset:
        """Buildings whose rally point is the army home. Empty means no rally."""
        return frozenset()

    def home_gather_excluded(self) -> frozenset:
        """Train-unit names that must not be walked to the army point."""
        return frozenset()

    def normalize_upgrade_name(self, upgrade_name: str) -> Optional[str]:
        """Map SC2 upgrade names to platform research targets."""
        return None

    @abstractmethod
    def is_building_target(self, target: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def create_tactics(self) -> Any:
        """Always-on Sharpy BuildOrder for mining / repair / light defense."""
