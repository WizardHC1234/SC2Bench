"""Race adapter public contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple

from sc2bench_env.runtime.task import DemandState, Task


class RaceAdapter(ABC):
    """Maps platform targets onto Sharpy Acts."""

    race_name: str = "unknown"
    townhall_targets: Optional[Tuple[str, ...]] = None

    def production_owned_count(self, snapshot: Any, action: str, target: str) -> int:
        """Observed count used only to convert extra demand to an Act target.

        The default uses the canonical target's raw facts, without race aliases.
        It does not credit output, count queued production or complete tasks.
        """
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

    def read_production_capacity(self, ai: Any) -> Optional[list]:
        """Read observed queue headroom, or None when not implemented.

        This is an observation hook, not a scheduler: do not reserve resources,
        issue commands or infer affordable starts from available queue slots.
        Race-specific fields and unknown values remain the adapter's facts.
        """
        return None

    @abstractmethod
    def create_act(self, task: Task, *, to_count: int) -> Any:
        """Return a Sharpy ActBase for the task."""

    @abstractmethod
    def normalize_unit_name(self, type_name: str) -> Optional[str]:
        """Map SC2/Sharpy type names to platform target keys."""

    def normalize_upgrade_name(self, upgrade_name: str) -> Optional[str]:
        """Map SC2 upgrade names to platform research targets."""
        return None

    @abstractmethod
    def is_building_target(self, target: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def create_tactics(self) -> Any:
        """Always-on Sharpy BuildOrder for mining / repair / light defense."""
