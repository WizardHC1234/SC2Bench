"""Protoss host/queue constraints and payment reconciliation.

Shared macro scheduling owns task order and soft budgets. These rules do not
issue commands, select a build order, cancel demands or infer tactical success.
"""

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts.act_unit import MAX_TRAIN_QUEUE

from sc2bench_env.backends.sharpy.races.protoss import BUILDINGS, GATEWAY_UNITS, UNITS
from sc2bench_env.interface.action_catalog import get_target
from sc2bench_env.runtime.task import DemandState


def execution_blocker(ai, task):
    action, target = task.get("action"), task.get("target")
    if action == "build" and target == "assimilator":
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
    if action == "train" and target in UNITS:
        _, producer_type = UNITS[target]
        producers = list(ai.structures(producer_type).ready)
        if target in GATEWAY_UNITS:
            producers += list(ai.structures(UnitTypeId.WARPGATE).ready)
        if producer_type == UnitTypeId.NEXUS:
            producers = list(ai.structures(UnitTypeId.NEXUS).ready)
        if not producers:
            return None
        spec = get_target(str(target), race="protoss")
        if spec is None:
            return None
        if all(len(getattr(parent, "orders", []) or []) >= MAX_TRAIN_QUEUE for parent in producers):
            return "producer_busy"
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
    if action == "build" and target == "nexus":
        return float(ai.already_pending(UnitTypeId.NEXUS)) > 0
    if action == "build" and target in BUILDINGS:
        return float(ai.already_pending(BUILDINGS[target])) > 0
    if action == "train" and target in UNITS:
        unit_type, _ = UNITS[target]
        return float(ai.already_pending(unit_type)) > 0
    return False


def ready_progress_target(task):
    action = str(task.get("action") or "")
    target = str(task.get("target") or "")
    if action == "build" and (target == "nexus" or target in BUILDINGS):
        return target
    if action == "train" and target in UNITS:
        return target
    return None


def ability_task_state(action, snapshot, waiting_for):
    """chrono_boost waits on a Nexus and energy. The cast itself marks completion."""
    if action != "chrono_boost":
        raise ValueError("unsupported Protoss ability task feedback: " + str(action))
    if int(snapshot.buildings.get("nexus", 0)) <= 0:
        return DemandState.WAITING_TO_START, "prerequisite:nexus"
    if waiting_for:
        return DemandState.WAITING_TO_START, waiting_for
    if int(snapshot.info.get("chrono_ready", 0)) > 0:
        return DemandState.IN_PROGRESS, None
    return DemandState.WAITING_TO_START, "energy"
