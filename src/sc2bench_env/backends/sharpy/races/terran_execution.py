"""Terran host/queue constraints and existing private payment reconciliation.

Shared macro scheduling owns task order and soft budgets. These rules do not
issue commands, select a build order, cancel demands or infer tactical success.
"""

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts.act_unit import MAX_TRAIN_QUEUE

from sc2bench_env.backends.sharpy.races.terran import ADDONS, BUILDINGS, TOWNHALL_TARGETS, UNITS
from sc2bench_env.interface.action_catalog import get_target
from sc2bench_env.runtime.task import DemandState


def execution_blocker(ai, task):
    action, target = task.get("action"), task.get("target")
    if action == "build" and target == "refinery":
        # Use BuildGas's selector. A probe failure is not a placement fact;
        # normal Act execution retains its existing error-reporting path.
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
    if action == "build" and target in ADDONS:
        _, parent_type = ADDONS[target]
        parents = list(ai.structures(parent_type).ready)
        if not parents:
            return None  # Catalog prerequisite checks report missing parents.
        if any(not isinstance(getattr(parent, "is_flying", None), bool)
               or not isinstance(getattr(parent, "add_on_tag", None), int)
               for parent in parents):
            return None
        hosts = [parent for parent in parents
                 if getattr(parent, "is_flying", None) is False
                 and getattr(parent, "add_on_tag", None) == 0]
        if not hosts:
            return "addon_host_unavailable"
        if all(bool(getattr(parent, "orders", [])) for parent in hosts):
            return "addon_host_busy"
    elif action == "train" and target in UNITS:
        _, producer_type = UNITS[target]
        producers = list(ai.structures(producer_type).ready)
        if producer_type == UnitTypeId.COMMANDCENTER:
            producers += list(ai.structures(UnitTypeId.ORBITALCOMMAND).ready)
            producers += list(ai.structures(UnitTypeId.PLANETARYFORTRESS).ready)
        if not producers:
            return None
        if any(not isinstance(getattr(parent, "is_flying", None), bool)
               for parent in producers):
            return None
        grounded = [parent for parent in producers if not parent.is_flying]
        if not grounded:
            return "producer_unavailable"
        spec = get_target(str(target), race="terran")
        addons = {int(addon.tag): addon for addon in ai.structures
                  if getattr(addon, "is_ready", False)}
        techlab_types = {ADDONS[req][0] for req in spec.prerequisites
                         if req in ADDONS and req.endswith("_techlab")}
        if techlab_types:
            grounded = [parent for parent in grounded
                        if getattr(addons.get(parent.add_on_tag), "type_id", None)
                        in techlab_types]
            if not grounded:
                return "producer_techlab_unavailable"
        if all(len(parent.orders) >= MAX_TRAIN_QUEUE for parent in grounded):
            return "producer_busy"
    return None


def resource_committed(ai, task):
    action = str(task.get("action") or "")
    target = str(task.get("target") or "")
    if (task.get("_resource_committed") and action == "train" and target in UNITS
            and float(ai.already_pending(UNITS[target][0])) <= 0):
        # Preserve existing reconciliation when a queue finishes or is lost.
        task.pop("_resource_committed", None)
        task.pop("_paid", None)
    if task.get("_resource_committed"):
        return True
    if action == "build" and target == "command_center":
        return float(ai.already_pending(UnitTypeId.COMMANDCENTER)) > 0
    if action == "build" and target in BUILDINGS:
        return float(ai.already_pending(BUILDINGS[target])) > 0
    if action == "build" and target in ADDONS:
        addon_type, _ = ADDONS[target]
        return float(ai.already_pending(addon_type)) > 0
    if action == "train" and target in UNITS:
        unit_type, _ = UNITS[target]
        return float(ai.already_pending(unit_type)) > 0
    return False


def ready_progress_target(task):
    action = str(task.get("action") or "")
    target = str(task.get("target") or "")
    if action == "build":
        if target in TOWNHALL_TARGETS:
            return "command_center"
        if target in BUILDINGS or target in ADDONS:
            return target
    if action == "train" and target in UNITS:
        return target
    return None


def ability_task_state(action, snapshot, waiting_for):
    """Retain scan/MULE feedback precedence; never infer cast completion."""
    if action not in {"scan", "call_mule"}:
        raise ValueError("unsupported Terran ability task feedback: " + str(action))
    if int(snapshot.buildings.get("orbital_command", 0)) <= 0:
        return DemandState.WAITING_TO_START, "prerequisite:orbital_command"
    if waiting_for:
        return DemandState.WAITING_TO_START, waiting_for
    ready_key = "scan_ready" if action == "scan" else "mule_ready"
    if int(snapshot.info.get(ready_key, 0)) > 0:
        return DemandState.IN_PROGRESS, None
    return DemandState.WAITING_TO_START, "energy"
