"""Orbital arbitration uses per-caster energy, not a pooled/old frame bank."""

import asyncio
from types import SimpleNamespace

import pytest

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
_ensure_runtime_paths()
from sc2bench_env.backends.sharpy.acts import ActCallMule, ActScanZone
from sc2bench_env.backends.sharpy.macro import ActOngoingMacroTasks
from sc2.position import Point2
from sc2bench_env.backends.sharpy.backend import SharpyBackend
from sc2bench_env.backends.base import BackendSnapshot
from sc2bench_env.runtime.task import DemandState


class Orbital:
    def __init__(self, ai, tag, energy, accepted=True):
        self.ai, self.tag, self.energy, self.accepted = ai, tag, energy, accepted

    def __call__(self, ability, target):
        if not self.accepted:
            return False
        self.ai.casts.append((self.tag, ability))
        self.ai.unit_tags_received_action.add(self.tag)
        # BurnySC2 does not decrement energy until the next observation.
        return True


def setup(energies, order=("scan", "call_mule"), accepted=True):
    ai = SimpleNamespace(minerals=500, vespene=500, supply_cap=30, supply_used=10,
                         unit_tags_received_action=set(), casts=[])
    orbitals = [Orbital(ai, i + 1, energy, accepted) for i, energy in enumerate(energies)]
    ai.structures = lambda unit_type: SimpleNamespace(ready=orbitals)
    ai.zone_registry = SimpleNamespace(resolve_zone=lambda *args: None,
                                       center_for=lambda zone: (10, 10))
    tasks = []
    for i, action in enumerate(order):
        act = ActScanZone("zone_0") if action == "scan" else ActCallMule()
        act.ai = ai
        act.zone_manager = SimpleNamespace()
        if action == "call_mule":
            act._solve_target = lambda: Point2((10, 10))
        tasks.append({"action": action, "target": "zone_0" if action == "scan" else "",
                      "order_index": i, "_act": act, "_started": True})
    macro = ActOngoingMacroTasks(tasks)
    macro.ai = ai
    macro._count_ready = lambda name: len(orbitals)
    return macro, tasks, ai, orbitals


@pytest.mark.parametrize("order", [("scan", "call_mule"), ("call_mule", "scan")])
def test_one_fifty_energy_orbital_only_completes_first_command(order):
    macro, tasks, ai, _ = setup([50], order)
    asyncio.run(macro.execute())
    assert len(ai.casts) == 1
    assert tasks[0]["_act_done"] and not tasks[1]["_act_done"]
    assert tasks[1]["_waiting_for"] == "energy"


def test_two_orbitals_can_cast_independently_in_one_frame():
    macro, tasks, ai, _ = setup([50, 50])
    asyncio.run(macro.execute())
    assert [tag for tag, _ in ai.casts] == [1, 2]
    assert all(task["_act_done"] for task in tasks)


def test_two_insufficient_orbitals_do_not_pool_energy():
    macro, tasks, ai, _ = setup([30, 30])
    asyncio.run(macro.execute())
    assert not ai.casts
    assert all(task["_waiting_for"] == "energy" for task in tasks)


def test_completed_cast_does_not_lose_completion_or_reserve_on_next_frame():
    macro, tasks, ai, orbitals = setup([50])
    asyncio.run(macro.execute())
    ai.unit_tags_received_action.clear()
    orbitals[0].energy = 0
    asyncio.run(macro.execute())
    assert tasks[0]["_act_done"] and "_waiting_for" not in tasks[0]
    assert len(ai.casts) == 1
    orbitals[0].energy = 50
    asyncio.run(macro.execute())
    assert tasks[1]["_act_done"] and len(ai.casts) == 2


@pytest.mark.parametrize("action", ["scan", "call_mule"])
def test_unaccepted_command_does_not_complete(action):
    _, tasks, ai, _ = setup([50], order=(action,), accepted=False)
    assert not asyncio.run(tasks[0]["_act"].execute())
    assert not tasks[0]["_act"]._done and not ai.casts


def test_high_energy_orbital_serializes_casts_until_the_next_frame():
    macro, tasks, ai, orbitals = setup([100])
    asyncio.run(macro.execute())
    assert len(ai.casts) == 1 and not tasks[1]["_act_done"]
    ai.unit_tags_received_action.clear()
    orbitals[0].energy = 50
    asyncio.run(macro.execute())
    assert len(ai.casts) == 2 and all(task["_act_done"] for task in tasks)


@pytest.mark.parametrize("action", ["scan", "call_mule"])
@pytest.mark.parametrize("orbital,ready,reason,expected", [
    (0, 0, None, "prerequisite:orbital_command"),
    (1, 0, None, "energy"),
    (1, 0, "energy", "energy"),
    (1, 1, "energy", "energy"),  # readiness is not a per-task reservation
])
def test_update_distinguishes_missing_orbital_from_unavailable_energy(action, orbital, ready, reason, expected):
    backend = SharpyBackend()
    from sc2bench_env.backends.sharpy.races.terran import TerranAdapter
    backend._adapter = TerranAdapter()
    bridge = backend._bridge
    bridge.snapshot = BackendSnapshot(buildings={"orbital_command": orbital},
                                      info={"scan_ready": ready, "mule_ready": ready})
    bridge.active_task_ids = ["test"]
    bridge.task_meta["test"] = (action, "zone_0" if action == "scan" else "", 1)
    if reason:
        bridge.waiting_reasons["test"] = reason
    update = backend.collect_updates()[0]
    assert update.state == DemandState.WAITING_TO_START and update.waiting_for == expected


def test_orbital_prerequisite_does_not_accept_an_ordinary_command_center():
    macro, _, ai, _ = setup([])
    del macro._count_ready
    ai.townhalls = SimpleNamespace(ready=SimpleNamespace(amount=1))
    ai.structures = lambda unit_type: SimpleNamespace(ready=[])
    assert macro._count_ready("command_center") == 1
    assert macro._count_ready("orbital_command") == 0
