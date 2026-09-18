"""Keep Sharpy Acts running for active platform tasks."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from sharpy.plans.acts.act_base import ActBase

from sc2bench_env.interface.action_catalog import get_target
from sc2bench_env.interface.races import require_supported_own_race
from sc2bench_env.backends.sharpy.races import get_adapter

logger = logging.getLogger("sc2bench_env.backends.sharpy.macro")

def _estimate_cost(task: Dict[str, Any], *, race: str = "terran") -> Dict[str, float]:
    action = str(task.get("action") or "")
    target = str(task.get("target") or "")
    to = str(task.get("to") or "")
    key = to if action == "upgrade" else (action if action in {"scan", "call_mule", "scout"} else target)
    if action == "combat":
        key = str(task.get("style") or "")
    spec = get_target(key, race=race)
    if spec is None:
        return {"minerals": 0.0, "vespene": 0.0, "supply": 0.0, "energy": 0.0}
    return {
        "minerals": float(spec.minerals),
        "vespene": float(spec.vespene),
        "supply": float(spec.supply if action == "train" else 0),
        "energy": float(spec.energy),
    }


def _catalog_key(task: Dict[str, Any]) -> str:
    action = str(task.get("action") or "")
    if action == "upgrade":
        return str(task.get("to") or "")
    if action in {"scan", "call_mule", "scout"}:
        return action
    if action == "combat":
        return str(task.get("style") or "")
    return str(task.get("target") or "")


class ActOngoingMacroTasks(ActBase):
    """Each frame: instantiate/execute Sharpy acts for active macro targets."""

    def __init__(self, active_tasks_ref: List[Dict[str, Any]], *, race: str = "terran"):
        require_supported_own_race(race)
        super().__init__()
        self.race = race
        self.adapter = get_adapter(race)
        self.active_tasks = active_tasks_ref

    def _count_ready(self, platform_name: str) -> int:
        return self.adapter.count_ready(self.ai, platform_name)

    def _prereq_blocked(self, task: Dict[str, Any]) -> bool:
        task.pop("_waiting_for", None)
        action = str(task.get("action") or "")
        if action == "scout":
            # Preserve the existing scout reporting boundary while taking the
            # required worker from the selected race catalog, not a fixed SCV.
            spec = get_target("scout", race=self.race)
            return any(self._count_ready(req) <= 0 for req in spec.prerequisites)
        if action == "combat":
            return False

        key = _catalog_key(task)
        spec = get_target(key, race=self.race)
        if spec is None:
            return False
        for req in spec.prerequisites:
            names = self.adapter.prerequisite_count_keys(req)
            if sum(self._count_ready(name) for name in names) <= 0:
                task["_waiting_for"] = f"prerequisite:{req}"
                return True
        return False

    def _execution_blocker(self, task: Dict[str, Any]) -> Optional[str]:
        """Observed host/slot constraints, not a guessed resource explanation.

        No placement or ability availability is inferred from an idle unit.
        Resource priority applies only to runnable unpaid work.
        """
        return self.adapter.execution_blocker(self.ai, task)

    def _resource_committed(self, task: Dict[str, Any]) -> bool:
        return self.adapter.resource_committed(self.ai, task)

    def _ready_progress(self, task: Dict[str, Any]) -> int:
        target = self.adapter.ready_progress_target(task)
        return self._count_ready(target) if target is not None else 0

    def _mark_paid_if_spent(self, task: Dict[str, Any], before_m: float, before_v: float) -> None:
        after_m = float(getattr(self.ai, "minerals", 0) or 0)
        after_v = float(getattr(self.ai, "vespene", 0) or 0)
        if after_m < before_m or after_v < before_v:
            task["_paid"] = True
            task["_resource_committed"] = True
        elif self._resource_committed(task):
            task["_resource_committed"] = True

    def _should_hold_budget(self, task: Dict[str, Any], to_count: int) -> bool:
        """Hold planning budget until this demand has actually spent for its next unit.

        Sharpy Expand/GridBuilding can return done as soon as a build is ordered,
        which may be before minerals leave the bank. Soft-reserve must continue
        until payment is observed. After a unit finishes, clear paid so the next
        absolute unit in the same act still reserves.
        """
        action = str(task.get("action") or "")
        if action in {"build", "train"}:
            progress = self._ready_progress(task)
            if progress >= to_count:
                return False
            prev = int(task.get("_ready_seen", progress))
            if progress > prev:
                task["_ready_seen"] = progress
                task.pop("_paid", None)
                task.pop("_resource_committed", None)
            else:
                task.setdefault("_ready_seen", progress)
            return not bool(task.get("_paid"))
        if action in {"research", "upgrade"}:
            return not bool(task.get("_paid"))
        return not bool(task.get("_act_done"))

    async def execute(self) -> bool:
        # Preserve JSON array spend priority (PLATFORM_PLAN §5.3).
        ordered = sorted(
            self.active_tasks,
            key=lambda task: (
                int(task.get("order_index", 10**9)),
                str(task.get("task_id") or ""),
            ),
        )

        budget_m = float(getattr(self.ai, "minerals", 0) or 0)
        budget_v = float(getattr(self.ai, "vespene", 0) or 0)
        budget_supply = max(
            0.0,
            float(getattr(self.ai, "supply_cap", 0) or 0)
            - float(getattr(self.ai, "supply_used", 0) or 0),
        )
        budget_e = self.adapter.ability_energy_budget(self.ai)

        for task in ordered:
            if task.get("_disabled", False):
                continue
            task.pop("_waiting_for", None)

            try:
                to_count = int(task.get("to_count", 1))
            except (TypeError, ValueError):
                task["_disabled"] = True
                task["_error"] = "invalid_to_count"
                continue

            act: Optional[ActBase] = task.get("_act")
            if act is None:
                factory: Optional[Callable[[], ActBase]] = task.get("_factory")
                if factory is None:
                    task["_disabled"] = True
                    task["_error"] = "no_action_factory"
                    continue
                try:
                    act = factory()
                except Exception as exc:
                    logger.warning(
                        "Failed to instantiate act for %s %s: %s",
                        task.get("action"),
                        task.get("target"),
                        exc,
                    )
                    task["_disabled"] = True
                    task["_error"] = f"instantiate_failed: {exc}"
                    continue
                task["_act"] = act
                task["_started"] = False

            # Keep absolute target in sync when TaskManager refreshes remaining work.
            if hasattr(act, "to_count"):
                act.to_count = to_count
            if hasattr(act, "target_count"):
                act.target_count = to_count

            if not task.get("_started", False):
                try:
                    await self.start_component(act, self.knowledge)
                except Exception as exc:
                    logger.warning("Failed to start act: %s", exc)
                    task["_disabled"] = True
                    task["_error"] = f"start_failed: {exc}"
                    continue
                task["_started"] = True

            cost = _estimate_cost(task, race=self.race)
            if task.get("action") == "combat":
                act.update_order(str(task["style"]), str(task["target"]),
                                 bool(task.get("withdrawing")), int(task.get("command_revision", 0)))
            if task.get("action") in {"scan", "call_mule"}:
                if getattr(act, "_done", False):
                    # Keep the acknowledged one-shot visible until TaskManager
                    # collects it, even if its cast left the Orbital below 50.
                    task["_act_done"] = True
                    task.pop("_waiting_for", None)
                    continue
                # Completed casts do not soft-reserve; instead remove their
                # casters from this frame's usable bank. Retain any earlier
                # soft reservation by taking the minimum, not resetting it.
                budget_e = min(budget_e, self.adapter.ability_energy_budget(self.ai, available_only=True))
            committed = self._resource_committed(task)

            def _soft_reserve() -> None:
                nonlocal budget_m, budget_v, budget_supply, budget_e
                budget_m = max(0.0, budget_m - cost["minerals"])
                budget_v = max(0.0, budget_v - cost["vespene"])
                budget_supply = max(0.0, budget_supply - cost["supply"])
                budget_e = max(0.0, budget_e - cost["energy"])

            if committed:
                try:
                    before_m = float(getattr(self.ai, "minerals", 0) or 0)
                    before_v = float(getattr(self.ai, "vespene", 0) or 0)
                    task["_act_done"] = bool(await act.execute())
                    failure = getattr(act, "failure_reason", None)
                    if failure:
                        task["_execution_error"] = str(failure)
                    else:
                        task.pop("_execution_error", None)
                    self._mark_paid_if_spent(task, before_m, before_v)
                    blocker = self._execution_blocker(task)
                    if blocker and not task["_act_done"]:
                        task["_waiting_for"] = blocker
                except Exception as exc:
                    logger.warning("Act execute failed: %s", exc)
                    task["_act_done"] = False
                    task["_execution_error"] = repr(exc)
                # Expand may report done once ordered, before the bank is charged.
                if self._should_hold_budget(task, to_count):
                    _soft_reserve()
                continue

            if self._prereq_blocked(task):
                # Missing prerequisites must not soft-reserve resources.
                task["_act_done"] = False
                continue

            blocker = self._execution_blocker(task)
            if blocker:
                task["_waiting_for"] = blocker
                task["_act_done"] = False
                continue

            can_fund = (
                cost["minerals"] <= budget_m
                and cost["vespene"] <= budget_v
                and cost["supply"] <= budget_supply
                and cost["energy"] <= budget_e
            )
            if not can_fund:
                # Soft-reserve so later cheaper demands cannot starve this one.
                task["_waiting_for"] = (
                    "supply" if cost["supply"] > budget_supply else
                    "energy" if cost["energy"] > budget_e else "resources"
                )
                _soft_reserve()
                task["_act_done"] = False
                continue

            try:
                before_m = float(getattr(self.ai, "minerals", 0) or 0)
                before_v = float(getattr(self.ai, "vespene", 0) or 0)
                task["_act_done"] = bool(await act.execute())
                failure = getattr(act, "failure_reason", None)
                if failure:
                    task["_execution_error"] = str(failure)
                else:
                    task.pop("_execution_error", None)
                self._mark_paid_if_spent(task, before_m, before_v)
                # Claim this demand's next unit cost until payment is observed.
                if self._should_hold_budget(task, to_count):
                    _soft_reserve()
            except Exception as exc:
                logger.warning("Act execute failed: %s", exc)
                task["_act_done"] = False
                task["_execution_error"] = repr(exc)

        return True
