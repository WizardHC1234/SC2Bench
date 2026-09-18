"""Reduce Agent bookkeeping without changing intentions, order or raw data."""
import asyncio
import copy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.unit.test_execution_blockers import macro
from sc2.position import Point2
from sc2.ids.unit_typeid import UnitTypeId as U
from sharpy.plans.acts.build_gas import BuildGas
from sc2bench_env.interface.observations import render_observation_text
from sc2bench_env.interface.observation_text import compact_counted, render_feedback_text, waiting_summary, production_options


class Collection(list):
    def filter(self, predicate):
        return Collection(x for x in self if predicate(x))

    def closer_than(self, distance, unit):
        return self.filter(lambda x: x.position.distance_to(unit.position) < distance)


def gas_macro(*, occupied=False, ready=True, remaining=2000, pending=0, foreign=False):
    m = macro()
    m._count_ready = lambda _: 1
    m._ready_progress = lambda _: 0
    geyser = NS(position=Point2((4, 0)), vespene_contents=remaining)
    base = NS(position=Point2((0, 0)), is_ready=ready, build_progress=1 if ready else .5)
    gas = NS(position=geyser.position, vespene_contents=2000, is_ready=True)
    m.ai.townhalls = Collection([base])
    m.ai.vespene_geyser = Collection([geyser])
    m.ai.gas_buildings = Collection([gas] if occupied and not foreign else [])
    act = BuildGas(2)
    act.ai = m.ai
    act.unit_type = U.REFINERY
    act.pending_build = MagicMock(return_value=pending)
    act.cache = NS(own=lambda _: [gas] if occupied and not foreign else [],
                   enemy=lambda _: [gas] if occupied and foreign else [])
    act.zone_manager = NS(own_main_zone=NS(center_location=base.position))
    task = {"action": "build", "target": "refinery", "to_count": 2,
            "_act": act, "_started": True, "order_index": 1}
    return m, task


@pytest.mark.parametrize("kwargs,blocked", [
    ({}, False), ({"occupied": True}, True),
    ({"occupied": True, "foreign": True}, True),
    ({"ready": False}, True), ({"remaining": 50}, True),
    ({"occupied": True, "pending": 1}, False),
])
def test_refinery_blocker_uses_actual_sharpy_site_selector(kwargs, blocked):
    m, task = gas_macro(**kwargs)
    assert m._execution_blocker(task) == ("no_available_geyser" if blocked else None)


def test_no_geyser_does_not_reserve_resources_and_recovers_when_site_is_available():
    m, first = gas_macro(occupied=True)
    m.ai.minerals, m.ai.vespene = 50, 0
    first["_act"].execute = AsyncMock(return_value=False)
    next_act = NS(execute=AsyncMock(return_value=False))
    second = {"action": "train", "target": "marine", "order_index": 2,
              "_act": next_act, "_started": True}
    m.active_tasks = [first, second]
    asyncio.run(m.execute())
    assert first["_waiting_for"] == "no_available_geyser"
    first["_act"].execute.assert_not_awaited()
    next_act.execute.assert_awaited_once()
    assert m.active_tasks == [first, second]  # No cancellation/reordering.
    m.ai.gas_buildings.clear()
    first["_act"].cache.own = lambda _: []
    m.ai.minerals = 200
    asyncio.run(m.execute())
    first["_act"].execute.assert_awaited_once()
    assert "_waiting_for" not in first


def test_completed_and_unknown_refinery_work_is_not_misreported_as_no_site():
    m, task = gas_macro(occupied=True)
    task["to_count"] = 1
    assert m._execution_blocker(task) is None
    task["_act"] = NS(execute=AsyncMock())
    assert m._execution_blocker(task) is None
    task["_act"] = NS(find_best=MagicMock(side_effect=RuntimeError("probe failed")),
                      best_gas=None, active_harvester_count=0,
                      pending_build=lambda _: 0, unit_type=U.REFINERY)
    task["to_count"] = 2
    assert m._execution_blocker(task) is None


def test_summary_preserves_mixed_blocker_quantities_and_ignores_paid_only_work():
    rows = [
        {"action": "train", "target": "marine", "remaining": 6,
         "waiting_to_produce": 4, "in_production": 2, "waiting_for": "resources"},
        {"action": "train", "target": "marine", "remaining": 3,
         "waiting_to_produce": 3, "waiting_for": "producer_busy"},
        {"action": "train", "target": "marine", "remaining": 1,
         "waiting_to_produce": 0, "waiting_for": "producer_busy"},
    ]
    observation = {"production_priority": rows, "training": {
        "marine": {"in_production": 3, "waiting_to_produce": 7, "order_progress": "2/8",
                   "waiting_for": "producer_busy"}}}
    original = copy.deepcopy(observation)
    text = render_observation_text(observation)
    assert "marine | 3 | 7 | resource budget unavailable" in text
    assert "earlier spending priority): 4; compatible production slots occupied: 3" in text
    assert "2/8" not in text
    assert observation == original


def test_unreported_and_unknown_waiting_quantities_are_not_fabricated():
    rows = [{"action": "train", "target": "marine", "remaining": 8,
             "waiting_to_produce": None}]
    assert waiting_summary("train", "marine", rows, {}) == "unknown: unknown"
    assert waiting_summary("train", "marine", [], {}) == "unknown"
    assert waiting_summary("train", "marine", [], {"waiting_to_produce": 0}) == "none"
    assert waiting_summary("build", "barracks", [], {
        "waiting_to_start": 0, "worker_en_route": 0}) == "none"


def test_full_priority_keeps_each_order_and_neutrally_labels_existing_work():
    rows = [{"action": "train", "target": "marine", "remaining": n} for n in (4, 4, 8)]
    rows += [{"action": "build", "target": "barracks", "remaining": 1}, rows[0].copy()]
    text = render_observation_text({"production_priority": rows})
    assert "1 | train | marine | 4" in text
    assert "2 | train | marine | 4" in text
    assert "1-2 |" not in text
    assert "3 | train | marine | 8" in text
    assert "4 | build | barracks | 1" in text
    assert "5 | train | marine | 4" in text
    assert len(rows) == 5
    assert "ALREADY ACCEPTED WORK" in text and "execution status and cancellable quantities" in text
    assert "do not resubmit" not in text
    assert "Paid train queue | Unqueued train | State | Waiting for | Cancellable" in text


def test_production_options_show_tech_facts_not_automatic_train_or_recommendations():
    capacity = [{"facility": "barracks", "ready_grounded": 3, "techlab_hosts": 0},
                {"facility": "factory", "ready_grounded": 4, "techlab_hosts": 0},
                {"facility": "starport", "ready_grounded": 4, "techlab_hosts": 0}]
    original = copy.deepcopy(capacity)
    text = "\n".join(production_options(capacity, {}))
    assert "marine (tech ready)" in text
    assert "marauder (missing ready attached barracks_techlab)" in text
    assert "hellion (tech ready)" in text and "widow_mine (tech ready)" in text
    assert "siege_tank (missing ready attached factory_techlab)" in text
    assert "medivac (tech ready)" in text and "viking (tech ready)" in text
    assert "NOT automatic production" in text and "not recommendations" in text
    assert "does not mean affordable or an available slot" in text
    assert capacity == original


def test_attached_lab_readiness_is_not_guessed_from_detached_building_inventory():
    capacity = [{"facility": "barracks", "ready_grounded": 1, "techlab_hosts": 0}]
    text = "\n".join(production_options(capacity, {"barracks_techlab": {"completed": 1}}))
    assert "marauder (missing ready attached barracks_techlab)" in text
    capacity[0]["techlab_hosts"] = 1
    assert "marauder (tech ready)" in "\n".join(production_options(capacity, {}))


def test_options_do_not_invent_ready_producers_or_missing_unknown_technology():
    assert production_options(None or [], {}) == []
    assert production_options([{"facility": "factory", "ready_grounded": None}], {}) == []
    text = "\n".join(production_options([
        {"facility": "factory", "ready_grounded": 1, "techlab_hosts": None}], None))
    assert "unknown readiness: attached factory_techlab" in text
    assert "unknown readiness: armory" in text


def test_receipts_compress_only_adjacent_simple_acceptances_and_keep_failures():
    simple = {"action": "build", "result": "accepted", "target": "starport", "count": 1}
    failure = {"action": "combat", "result": "rejected", "reason": "insufficient_units",
               "details": {"requested": {"marine": 8}, "available": {"marine": 3}, "missing": {"marine": 5}}}
    feedback = {"receipts": [simple.copy() for _ in range(12)] + [failure, simple.copy()],
                "events": [{"type": "combat_ended", "group": "group_1", "end_reason": "force_destroyed"}]}
    original = copy.deepcopy(feedback)
    text = render_feedback_text(feedback)
    assert "Count: 12" in text and "Orders: 12" in text
    assert "Count: 1\n" in text
    assert "Missing: marine 5" in text and "force_destroyed" in text
    assert len(compact_counted(feedback["receipts"])) == 3
    assert feedback == original


def test_acceptance_events_compress_without_merging_birth_or_mission_events():
    event = {"type": "demand_accepted", "action": "train", "target": "marine", "count": 4}
    birth = {"type": "train_completed", "target": "marine", "count": 4}
    events = [event.copy(), event.copy(), birth.copy(), birth.copy()]
    original = copy.deepcopy(events)
    text = render_feedback_text({"receipts": [], "events": events})
    assert "Count: 8" in text and "Event count: 2" in text
    assert text.count("train_completed") == 2
    assert events == original
