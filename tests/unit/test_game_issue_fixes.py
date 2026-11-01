"""Confirmed observation, occupancy and overlord-source fixes."""
from types import SimpleNamespace

from sharpy.managers.core.roles import UnitTask

from sc2bench_env.backends.base import BackendSnapshot
from sc2bench_env.backends.sharpy.combat_styles import unit_available_for_background
from sc2bench_env.backends.sharpy.state_reader import read_snapshot
from sc2bench_env.interface.observations import render_observation_text
from sc2bench_env.runtime.observation_builder import combat_summary, own_forces_view
from sc2bench_env.runtime.task import DemandState
from sc2bench_env.runtime.task_manager import DemandUpdate, TaskManager
from tests.helpers.drive import submit_calls


def _economy():
    return {
        "minerals": 200, "vespene": 50, "supply_used": 12, "supply_cap": 14,
        "supply_left": 2, "army_supply": 0, "worker_count": 12,
        "workers_on_minerals": 12, "mineral_worker_saturation": 16,
        "workers_on_vespene": 0, "vespene_worker_saturation": 0, "workers_other": 0,
        "mineral_income_per_minute": 600, "vespene_income_per_minute": 0,
    }


def test_later_observation_keeps_the_race_worker_name():
    previous = {"game": {"game_time_seconds": 0, "seconds_remaining": 120}}
    for race, worker in (("terran", "SCVs"), ("protoss", "Probes"), ("zerg", "Drones")):
        observation = {
            "game": {
                "race": race, "enemy_race": "terran",
                "game_time_seconds": 12, "seconds_remaining": 108,
                "game_time_limit_seconds": 120,
            },
            "economy": _economy(),
        }
        opening = render_observation_text(observation)
        later = render_observation_text(observation, previous=previous)
        assert f"Workers ({worker})" in opening
        assert f"Workers ({worker})" in later
        assert "Own race" not in later
        assert "Enemy race" not in later


def _advance():
    return {"name": "advance", "arguments": {"seconds": 5}}


def test_morph_reservation_is_removed_from_dispatchable_counts():
    manager = TaskManager(race="zerg")
    submit_calls(
        manager,
        [{"name": "train", "arguments": {"target": "baneling", "count": 10}}, _advance()],
        game_time=0, race="zerg", idle_army={"zergling": 14},
    )
    snapshot = BackendSnapshot(units={"zergling": 14, "drone": 12})
    view = own_forces_view(snapshot, manager)
    assert view.free.get("zergling", 0) == 4
    assert view.army["zergling"] == 14


def test_group_counts_are_reconciled_with_same_frame_inventory():
    manager = TaskManager(race="terran")
    submit_calls(
        manager,
        [{"name": "dispatch_group", "arguments": {
            "source": "group_0", "target": "zone_1", "order": "attack",
            "units": {"marine": 16},
        }}, _advance()],
        game_time=0, race="terran", idle_army={"marine": 16},
    )
    demand = manager.active_demands()[0]
    snapshot = BackendSnapshot(
        units={"marine": 15},
        info={"combat_progress": {demand.demand_id: {
            "alive": {"marine": 16}, "assigned": True,
        }}},
    )

    combat = combat_summary(snapshot, manager)
    forces = own_forces_view(snapshot, manager, combat=combat)

    assert combat["group_1"]["alive"] == {"marine": 15}
    assert combat["group_0"]["alive"] == {}
    assert forces.army == {"marine": 15}
    assert forces.assigned == {"marine": 15}


def test_zerg_queen_can_defend_but_cannot_join_an_attack_group():
    attacking = TaskManager(race="zerg")
    receipts = submit_calls(
        attacking,
        [{"name": "dispatch_group", "arguments": {
            "source": "group_0", "target": "zone_1", "order": "attack",
            "units": {"queen": 1},
        }}, _advance()],
        game_time=0, race="zerg", idle_army={"queen": 1},
    )
    assert receipts[0].result == "rejected"
    assert receipts[0].reason == "queen_attack_not_supported"

    defending = TaskManager(race="zerg")
    receipts = submit_calls(
        defending,
        [{"name": "dispatch_group", "arguments": {
            "source": "group_0", "target": "zone_1", "order": "defend",
            "units": {"queen": 1},
        }}, _advance()],
        game_time=0, race="zerg", idle_army={"queen": 1},
    )
    assert receipts[0].result == "accepted"


def test_zerg_queen_defense_group_cannot_be_retargeted_to_attack():
    manager = TaskManager(race="zerg")
    submit_calls(
        manager,
        [{"name": "dispatch_group", "arguments": {
            "source": "group_0", "target": "zone_1", "order": "defend",
            "units": {"queen": 1},
        }}, _advance()],
        game_time=0, race="zerg", idle_army={"queen": 1},
    )
    receipts = submit_calls(
        manager,
        [{"name": "attack_group", "arguments": {
            "group": "group_1", "target": "zone_2",
        }}, _advance()],
        game_time=1, race="zerg", idle_army={},
        group_army={"group_1": {"queen": 1}},
    )
    assert receipts[0].result == "rejected"
    assert receipts[0].reason == "queen_attack_not_supported"


def test_zerg_queen_cannot_be_transferred_or_merged_into_attack_group():
    manager = TaskManager(race="zerg")
    submit_calls(
        manager,
        [
            {"name": "dispatch_group", "arguments": {
                "source": "group_0", "target": "zone_2", "order": "attack",
                "units": {"zergling": 4},
            }},
            {"name": "dispatch_group", "arguments": {
                "source": "group_0", "target": "zone_1", "order": "defend",
                "units": {"queen": 1},
            }},
            _advance(),
        ],
        game_time=0, race="zerg", idle_army={"zergling": 4, "queen": 1},
    )
    groups = {"group_1": {"zergling": 4}, "group_2": {"queen": 1}}

    reinforce = submit_calls(
        manager,
        [{"name": "reinforce_group", "arguments": {
            "source": "group_2", "destination": "group_1",
            "units": {"queen": 1},
        }}, _advance()],
        game_time=1, race="zerg", idle_army={}, group_army=groups,
    )
    assert reinforce[0].reason == "queen_attack_not_supported"

    merge = submit_calls(
        manager,
        [{"name": "merge_groups", "arguments": {
            "destination": "group_1", "sources": ["group_2"],
        }}, _advance()],
        game_time=2, race="zerg", idle_army={}, group_army=groups,
    )
    assert merge[0].reason == "queen_attack_not_supported"


def test_live_morph_reservation_is_reported_as_unavailable_army():
    class Units(list):
        @property
        def ready(self):
            return Units(unit for unit in self if getattr(unit, "is_ready", True))

    zergling = SimpleNamespace(
        tag=7,
        type_id=SimpleNamespace(name="ZERGLING"),
        is_structure=False,
        is_ready=True,
        is_hallucination=False,
        build_progress=1,
        orders=[],
    )
    ai = SimpleNamespace(
        structures=Units(),
        units=Units([zergling]),
        townhalls=Units(),
        gas_buildings=Units(),
        bench_group0_tags={7},
        bench_combat_tags=set(),
        bench_morph_tags={7},
    )
    adapter = SimpleNamespace(
        race_name="zerg",
        townhall_targets=("hatchery", "lair", "hive"),
        normalize_unit_name=lambda name: name.lower(),
        home_gather_excluded=lambda: frozenset(),
    )

    snapshot, *_ = read_snapshot(ai, adapter)

    assert snapshot.units == {"zergling": 1}
    assert snapshot.info["unavailable_army"] == {"zergling": 1}


def test_snapshot_keeps_gateway_and_warpgate_as_distinct_buildings():
    class Units(list):
        @property
        def ready(self):
            return Units(unit for unit in self if getattr(unit, "is_ready", True))

    def structure(name, tag):
        return SimpleNamespace(
            tag=tag,
            type_id=SimpleNamespace(name=name),
            is_structure=True,
            is_ready=True,
            build_progress=1,
            orders=[],
        )

    ai = SimpleNamespace(
        structures=Units([structure("GATEWAY", 1), structure("WARPGATE", 2)]),
        units=Units(),
        townhalls=Units(),
        gas_buildings=Units(),
    )
    adapter = SimpleNamespace(
        race_name="protoss",
        townhall_targets=("nexus",),
        normalize_unit_name=lambda name: "gateway" if name in {"GATEWAY", "WARPGATE"} else name.lower(),
    )

    snapshot, *_ = read_snapshot(ai, adapter)

    assert snapshot.buildings == {"gateway": 1, "warpgate": 1}
    assert snapshot.info["building_entity_tags"] == {"gateway": [1], "warpgate": [2]}


def test_same_batch_reserves_sources_in_submit_order():
    morph_first = TaskManager(race="zerg")
    receipts = submit_calls(
        morph_first,
        [
            {"name": "train", "arguments": {"target": "baneling", "count": 10}},
            {"name": "combat", "arguments": {"style": "attack", "target": "zone_1", "units": {"zergling": 14}}},
            _advance(),
        ],
        game_time=0, race="zerg", idle_army={"zergling": 14},
    )
    assert receipts[1].result == "rejected"
    assert receipts[1].reason == "insufficient_units"
    assert receipts[1].group is None
    assert morph_first.group_counter == 0

    combat_first = TaskManager(race="zerg")
    receipts = submit_calls(
        combat_first,
        [
            {"name": "combat", "arguments": {"style": "attack", "target": "zone_1", "units": {"zergling": 14}}},
            {"name": "train", "arguments": {"target": "baneling", "count": 10}},
            _advance(),
        ],
        game_time=0, race="zerg", idle_army={"zergling": 14},
    )
    assert receipts[0].result == "accepted"
    assert receipts[0].group == "group_1"
    assert receipts[1].result == "accepted"
    snapshot = BackendSnapshot(units={"zergling": 14})
    assert own_forces_view(snapshot, combat_first).free.get("zergling", 0) == 0


def test_realtime_shortage_names_the_group_and_counts():
    manager = TaskManager(race="zerg")
    submit_calls(
        manager,
        [{"name": "combat", "arguments": {"style": "attack", "target": "zone_1", "units": {"zergling": 4}}}, _advance()],
        game_time=0, race="zerg", idle_army={"zergling": 4},
    )
    demand = manager.active_demands()[0]
    manager.apply_updates(
        [DemandUpdate(
            demand_id=demand.demand_id, action="combat", target="zone_1",
            failure_reason="insufficient_units:zergling:requested=4:available=1:missing=3",
            state=DemandState.FAILED,
        )],
        game_time=2,
    )
    assert demand not in manager.active_demands()
    event = manager.recent_events[-1]
    assert event["type"] == "demand_failed"
    assert event["group"] == "group_1"
    assert event["requested"] == {"zergling": 4}
    assert event["available"] == {"zergling": 1}
    assert event["missing"] == {"zergling": 3}


class _Roles:
    def __init__(self, roles):
        self._roles = roles

    def is_in_role(self, role, unit):
        return role in self._roles.get(unit.tag, set())


def _overlord(tag, *, orders=None, cargo=0):
    return SimpleNamespace(
        tag=tag, is_ready=True, is_hallucination=False,
        orders=orders or [], cargo_used=cargo,
        type_id=SimpleNamespace(name="OVERLORD"),
    )


def test_idle_overlord_stays_morphable_when_only_reserved_for_position():
    unit = _overlord(7, orders=[SimpleNamespace(ability=SimpleNamespace(id=SimpleNamespace(name="MOVE_MOVE")))])
    ai = SimpleNamespace(
        bench_combat_tags=set(), bench_group0_tags=set(), bench_bunker_tags=set(),
        unit_tags_received_action=set(), roles=_Roles({7: {UnitTask.Reserved}}),
    )
    assert unit_available_for_background(unit, ai, require_group0=False)
    scouting = _overlord(8)
    ai.roles = _Roles({8: {UnitTask.Scouting}})
    assert not unit_available_for_background(scouting, ai, require_group0=False)
    casting = _overlord(9, orders=[SimpleNamespace(ability=SimpleNamespace(id=SimpleNamespace(name="MORPH_OVERSEER")))])
    ai.roles = _Roles({})
    assert not unit_available_for_background(casting, ai, require_group0=False)


def test_warp_gate_morph_reserves_a_busy_gateway_from_later_trains(monkeypatch):
    import asyncio
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2bench_env.backends.sharpy.races import protoss_warp
    from sc2bench_env.backends.sharpy.races.protoss_warp import ActGatewayTrain, ActWarpGateConversion

    class UnitCollection(list):
        @property
        def ready(self):
            return self

        @property
        def idle(self):
            return UnitCollection(item for item in self if not item.orders)

        @property
        def amount(self):
            return len(self)

    monkeypatch.setattr(protoss_warp, "warp_research_ready", lambda _ai: True)
    busy = SimpleNamespace(tag=1, orders=["train"], is_powered=True)
    morph = ActWarpGateConversion(1)
    morph.ai = SimpleNamespace(
        structures=lambda unit_type: UnitCollection([busy] if unit_type == UnitTypeId.GATEWAY else []),
        unit_tags_received_action=set(),
        bench_warp_reserve_tags=set(),
    )
    morph.reserve_pending_sources()
    assert morph.ai.bench_warp_reserve_tags == {1}
    assert asyncio.run(morph.execute()) is False
    assert morph.waiting_reason == "waiting_for_queue"

    trained = []
    busy.train = lambda unit_type: trained.append(unit_type)
    busy.orders = []
    train = ActGatewayTrain(UnitTypeId.ZEALOT, 1)
    train.ai = SimpleNamespace(
        units=lambda _unit_type: UnitCollection(),
        structures=lambda unit_type: UnitCollection([busy] if unit_type == UnitTypeId.GATEWAY else []),
        already_pending=lambda _unit_type: 0,
        unit_tags_received_action=set(),
        bench_warp_reserve_tags=morph.ai.bench_warp_reserve_tags,
        can_afford=lambda _unit_type: True,
    )
    assert asyncio.run(train.execute()) is False
    assert trained == []
    assert train.waiting_reason == "producer_busy"


def test_events_carry_ids_and_feedback_owns_the_current_step():
    manager = TaskManager(race="terran")
    manager.begin_decision_step(game_time=10)
    submit_calls(
        manager,
        [{"name": "train", "arguments": {"target": "scv", "count": 1}}, _advance()],
        game_time=10, race="terran",
    )
    first = manager.events_since_boundary()
    assert first and all("event_id" in event and event["game_time_seconds"] == 10 for event in first)
    manager.begin_decision_step(game_time=20)
    historical = manager.historical_events(limit=8)
    assert {event["event_id"] for event in first} <= {event["event_id"] for event in historical}
    assert manager.events_since_boundary() == []


def test_production_priority_text_keeps_compact_per_request_rows():
    rows = [
        {"action": "train", "target": "zealot", "state": "waiting_to_start",
         "order_progress": "0/2",
         "remaining": 2, "in_production": 0, "waiting_to_produce": 2,
         "waiting_for": "resources", "cancellable_count": 2, "mechanism": "queue"},
        {"action": "train", "target": "zealot", "state": "waiting_to_start",
         "order_progress": "0/3",
         "remaining": 3, "in_production": 0, "waiting_to_produce": 3,
         "waiting_for": "resources", "cancellable_count": 3, "mechanism": "queue"},
        {"action": "train", "target": "zealot", "state": "waiting_to_start",
         "order_progress": "0/1",
         "remaining": 1, "in_production": 0, "waiting_to_produce": 1,
         "waiting_for": "supply", "cancellable_count": 1, "mechanism": "queue"},
    ]
    text = render_observation_text({"production_priority": rows})
    assert "Active macro requests: 3 grouped into 2 lines" in text
    assert "1. train zealot; requests 2; remaining 5; paid 0; unstarted 5" in text
    assert "2. train zealot; remaining 1; paid 0; unstarted 1" in text
    assert "Progress by task" not in text and "Mechanism" not in text
