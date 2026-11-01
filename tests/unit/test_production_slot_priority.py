"""Production starts use live lanes without pre-filling non-preemptible queues."""
from types import SimpleNamespace as NS

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths

_ensure_runtime_paths()

from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts import ActUnit

from sc2bench_env.backends.sharpy.races import protoss_execution, terran_execution, zerg_execution
from sc2bench_env.backends.sharpy.macro import ActOngoingMacroTasks


class Structures(list):
    def __call__(self, unit_type):
        return NS(ready=[item for item in self if item.type_id == unit_type])


def structure(unit_type, *, tag=1, addon=0, orders=()):
    return NS(
        type_id=unit_type,
        tag=tag,
        add_on_tag=addon,
        orders=list(orders),
        is_ready=True,
        is_flying=False,
    )


def test_act_unit_uses_only_live_slots_and_appends_reactor_second_lane():
    act = ActUnit(UnitTypeId.MARINE, UnitTypeId.BARRACKS)
    reactor = structure(UnitTypeId.BARRACKSREACTOR, tag=9)
    act.cache = NS(by_tag=lambda tag: reactor if tag == 9 else None)

    normal = structure(UnitTypeId.BARRACKS)
    assert act.has_order_ready(normal)
    normal.orders.append(object())
    assert not act.has_order_ready(normal)

    reactored = structure(UnitTypeId.BARRACKS, addon=9, orders=[object()])
    assert act.has_order_ready(reactored)
    reactored.orders.append(object())
    assert not act.has_order_ready(reactored)

    issued = []
    reactored.orders.pop()
    reactored.train = lambda unit_type, queue=False: issued.append((unit_type, queue)) or True
    assert act._issue_train(reactored)
    assert issued == [(UnitTypeId.MARINE, True)]


def test_basic_terran_units_preserve_techlab_lane_when_other_hosts_exist():
    act = ActUnit(UnitTypeId.MARINE, UnitTypeId.BARRACKS)
    reactor = structure(UnitTypeId.BARRACKSREACTOR, tag=10)
    techlab = structure(UnitTypeId.BARRACKSTECHLAB, tag=11)
    addons = {10: reactor, 11: techlab}
    act.cache = NS(by_tag=lambda tag: addons.get(tag))

    reactored = structure(UnitTypeId.BARRACKS, tag=1, addon=10, orders=[object()])
    plain = structure(UnitTypeId.BARRACKS, tag=2)
    lab_host = structure(UnitTypeId.BARRACKS, tag=3, addon=11)

    ranked = sorted(
        [lab_host, plain, reactored],
        key=lambda builder: (
            act.addon_priority(builder),
            len(builder.orders) / max(1, act.parallel_slots(builder)),
            builder.tag,
        ),
    )
    assert ranked == [reactored, plain, lab_host]


def test_terran_blocker_reports_physical_slots_not_unused_queue_positions():
    reactor = structure(UnitTypeId.BARRACKSREACTOR, tag=9)
    barracks = structure(UnitTypeId.BARRACKS, addon=9, orders=[object()])
    ai = NS(structures=Structures([barracks, reactor]))
    task = {"action": "train", "target": "marine"}

    assert terran_execution.execution_blocker(ai, task) is None
    barracks.orders.append(object())
    assert terran_execution.execution_blocker(ai, task) == "producer_busy"


def test_protoss_normal_producer_does_not_fill_waiting_queue():
    nexus = structure(UnitTypeId.NEXUS, orders=[object()])
    ai = NS(structures=Structures([nexus]))
    assert protoss_execution.execution_blocker(
        ai, {"action": "train", "target": "probe"},
    ) == "producer_busy"


def test_zerg_queen_producer_does_not_fill_waiting_queue():
    hatchery = structure(UnitTypeId.HATCHERY, orders=[object()])
    ai = NS(structures=Structures([hatchery]))
    assert zerg_execution.execution_blocker(
        ai, {"action": "train", "target": "queen"},
    ) == "producer_busy"


def test_aggregate_pending_state_is_not_claimed_by_every_same_target_demand():
    first = {
        "task_id": "first", "order_index": 1,
        "action": "train", "target": "marine",
    }
    second = {
        "task_id": "second", "order_index": 2,
        "action": "train", "target": "marine",
    }
    macro = ActOngoingMacroTasks([first, second], race="terran")
    macro.ai = object()
    macro.adapter = NS(resource_committed=lambda _ai, _task: True)

    assert macro._resource_committed(first) is True
    assert macro._resource_committed(second) is False

    second["_resource_committed"] = True
    assert macro._resource_committed(second) is True
