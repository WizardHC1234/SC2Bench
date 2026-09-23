"""Zerg host/queue constraints and payment reconciliation.

Shared macro scheduling owns task order and soft budgets. These rules do not
issue commands, select a build order, cancel demands or infer tactical success.
"""

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts.act_unit import MAX_TRAIN_QUEUE

from sc2bench_env.backends.sharpy.races.zerg import BUILDINGS, LARVA_UNITS, UNITS
from sc2bench_env.interface.action_catalog import get_target
from sc2bench_env.runtime.task import DemandState


def execution_blocker(ai, task):
    action, target = task.get("action"), task.get("target")
    if action == "build" and target == "extractor":
        act = task.get("_act")
        finder = getattr(act, "find_best", None)
        if callable(finder) and hasattr(act, "best_gas"):
            try:
                if act.active_harvester_count >= int(task.get("to_count", 1)):
                    return None
                if act.pending_build(act.unit_type):
                    return None
                finder()
                if act.best_gas is None:
                    return "no_available_geyser"
            except Exception:
                return None
    if action == "train" and target in LARVA_UNITS:
        if not list(ai.units(UnitTypeId.LARVA)):
            return "producer_busy"
    if action == "train" and target == "queen":
        producers = []
        for unit_type in (UnitTypeId.HATCHERY, UnitTypeId.LAIR, UnitTypeId.HIVE):
            producers.extend(ai.structures(unit_type).ready)
        if producers and all(len(getattr(parent, "orders", []) or []) >= MAX_TRAIN_QUEUE
                             for parent in producers):
            return "producer_busy"
        spec = get_target("queen", race="zerg")
        if spec is None:
            return None
    return None


def resource_committed(ai, task):
    action = str(task.get("action") or "")
    target = str(task.get("target") or "")
    if (task.get("_resource_committed") and action == "train" and target in UNITS
            and float(ai.already_pending(UNITS[target][0])) <= 0):
        task.pop("_resource_committed", None)
        task.pop("_paid", None)
    if task.get("_resource_committed"):
        return True
    if action == "build" and target == "hatchery":
        return float(ai.already_pending(UnitTypeId.HATCHERY)) > 0
    if action == "build" and target in BUILDINGS:
        return float(ai.already_pending(BUILDINGS[target])) > 0
    if action == "train" and target in UNITS:
        unit_type, _ = UNITS[target]
        return float(ai.already_pending(unit_type)) > 0
    return False


def ready_progress_target(task):
    action = str(task.get("action") or "")
    target = str(task.get("target") or "")
    if action == "build" and (target == "hatchery" or target in BUILDINGS):
        return target
    if action == "train" and target in UNITS:
        return target
    return None


def ability_task_state(action, snapshot, waiting_for):
    """Queen energy casts wait on a Queen. The cast itself marks completion."""
    if action not in {"inject_larva", "spawn_creep_tumor"}:
        raise ValueError("unsupported Zerg ability task feedback: " + str(action))
    if int(snapshot.units.get("queen", 0)) <= 0:
        return DemandState.WAITING_TO_START, "prerequisite:queen"
    if waiting_for:
        return DemandState.WAITING_TO_START, waiting_for
    energies = snapshot.info.get("queen_energies") or []
    if any(float(energy) >= 25 for energy in energies):
        return DemandState.IN_PROGRESS, None
    return DemandState.WAITING_TO_START, "energy"
