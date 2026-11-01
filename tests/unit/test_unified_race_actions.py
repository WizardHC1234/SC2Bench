"""One public action vocabulary across Terran, Protoss and Zerg."""
from __future__ import annotations

import pytest

from tests.helpers.drive import submit_calls
from sc2bench_env.catalog.registry import get_target, known_target_names
from sc2bench_env.interface.actions import ActionValidationError, parse_decision
from sc2bench_env.runtime.task import DemandState
from sc2bench_env.runtime.task_manager import DemandUpdate, TaskManager


def _combat(units, *, race):
    return parse_decision(
        [
            {"name": "combat", "arguments": {"style": "attack", "target": "zone_1", "units": units}},
            {"name": "advance", "arguments": {"seconds": 5}},
        ],
        race=race,
    )


def test_upgrade_and_cc_ids_are_rejected():
    with pytest.raises(ActionValidationError):
        parse_decision(
            [{"name": "upgrade", "arguments": {"target": "townhall_0", "to": "orbital_command"}},
             {"name": "advance", "arguments": {"seconds": 5}}],
            race="terran",
        )
    with pytest.raises(ActionValidationError):
        parse_decision(
            [{"name": "morph_townhall", "arguments": {"target": "cc_0", "to": "orbital_command"}},
             {"name": "advance", "arguments": {"seconds": 5}}],
            race="terran",
        )


def test_townhall_morph_is_shared_by_terran_and_zerg_and_hidden_for_protoss():
    terran = parse_decision(
        [{"name": "morph_townhall", "arguments": {"target": "townhall_0", "to": "orbital_command"}},
         {"name": "advance", "arguments": {"seconds": 5}}],
        race="terran",
    )
    zerg = parse_decision(
        [{"name": "morph_townhall", "arguments": {"target": "townhall_0", "to": "lair"}},
         {"name": "advance", "arguments": {"seconds": 5}}],
        race="zerg",
    )
    assert terran.actions[0].action == "morph_townhall"
    assert zerg.actions[0].to == "lair"
    assert known_target_names("morph_townhall", race="protoss") == ()
    with pytest.raises(ActionValidationError):
        parse_decision(
            [{"name": "morph_townhall", "arguments": {"target": "townhall_0", "to": "lair"}},
             {"name": "advance", "arguments": {"seconds": 5}}],
            race="protoss",
        )


def test_combat_uses_catalog_dispatchable_units():
    _combat({"zealot": 1}, race="protoss")
    _combat({"queen": 1}, race="zerg")
    for race, unit in (("terran", "scv"), ("protoss", "probe"), ("zerg", "drone"), ("zerg", "overlord")):
        with pytest.raises(ActionValidationError):
            _combat({unit: 1}, race=race)


def test_production_mechanisms_follow_the_catalog():
    zergling = get_target("zergling", race="zerg")
    assert zergling.mechanism == "larva"
    assert zergling.production_batch_size == 2
    archon = get_target("archon", race="protoss")
    assert archon.mechanism == "merge"
    assert archon.minerals == 0 and archon.vespene == 0 and archon.supply == 0
    assert archon.dispatchable is True
    warpgate = get_target("warpgate", race="protoss")
    assert warpgate.mechanism == "structure_morph"
    assert warpgate.produced_at == "gateway"
    assert get_target("queen", race="zerg").mechanism == "queue"
    assert get_target("queen", race="zerg").dispatchable is True
    assert get_target("lurker_den", race="zerg").mechanism == "construct"
    assert get_target("lurker_den", race="zerg").morph_from == ""
    assert get_target("greater_spire", race="zerg").morph_from == "spire"
    assert get_target("orbital_command", race="terran").action == "morph_townhall"
    assert get_target("orbital_command", race="terran").mechanism == "structure_morph"


def test_unstarted_townhall_morph_can_be_cancelled_by_form():
    manager = TaskManager(race="zerg")
    submit_calls(
        manager,
        [{"name": "morph_townhall", "arguments": {"target": "townhall_0", "to": "lair"}},
         {"name": "advance", "arguments": {"seconds": 5}}],
        game_time=0,
        race="zerg",
    )
    assert manager.active_demands()[0].mechanism == "structure_morph"
    submit_calls(
        manager,
        [{"name": "cancel", "arguments": {"target_action": "morph_townhall", "target": "lair"}},
         {"name": "advance", "arguments": {"seconds": 5}}],
        game_time=1,
        race="zerg",
    )
    assert manager.active_demands() == []


def test_batch_train_reports_requested_and_actual_counts():
    manager = TaskManager(race="zerg")
    submit_calls(
        manager,
        [{"name": "train", "arguments": {"target": "zergling", "count": 1}},
         {"name": "advance", "arguments": {"seconds": 5}}],
        game_time=0,
        race="zerg",
    )
    demand = manager.active_demands()[0]
    assert demand.mechanism == "larva"
    assert demand.production_batch_size == 2
    manager.apply_updates(
        [DemandUpdate(demand_id=demand.demand_id, action="train", target="zergling",
                      produced_delta=2, state=DemandState.COMPLETED)],
        game_time=1,
    )
    assert demand.produced == 1
    assert demand.actual_produced == 2
    assert demand.state == DemandState.COMPLETED
    split = TaskManager(race="zerg")
    submit_calls(
        split,
        [{"name": "train", "arguments": {"target": "zergling", "count": 1}},
         {"name": "advance", "arguments": {"seconds": 5}}],
        game_time=0,
        race="zerg",
    )
    pending = split.active_demands()[0]
    split.apply_updates(
        [DemandUpdate(demand_id=pending.demand_id, action="train", target="zergling",
                      produced_delta=1, state=DemandState.IN_PRODUCTION)],
        game_time=1,
    )
    assert pending.state != DemandState.COMPLETED
    assert pending.actual_produced == 1
    split.apply_updates(
        [DemandUpdate(demand_id=pending.demand_id, action="train", target="zergling",
                      produced_delta=1, state=DemandState.COMPLETED)],
        game_time=2,
    )
    assert pending.actual_produced == 2
    assert pending.state == DemandState.COMPLETED
    event = next(item for item in manager.recent_events if item["type"] == "train_completed")
    assert event["requested_count"] == 1
    assert event["actual_produced"] == 2
    assert event["production_batch_size"] == 2
