"""Regression tests for actual mission logic without launching SC2."""

from types import SimpleNamespace

import pytest

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths, SharpyBackend

_ensure_runtime_paths()

from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2
from sc2.units import Units
from sc2bench_env.backends.sharpy.acts import ActCombatMission
from sc2bench_env.backends.sharpy.bot import BenchBot


class UnitStub:
    def __init__(self, tag, type_id=UnitTypeId.MARINE, position=(0, 0), flying=False, **kwargs):
        self.tag, self.type_id = tag, type_id
        self.position = Point2(position)
        self.is_flying = flying
        self.is_visible = self.is_ready = True
        self.is_structure = self.is_snapshot = self.is_memory = self.is_hallucination = False
        self.ground_weapon = not flying
        self.air_weapon = False
        self.power = 1.0
        self.passengers = []
        self.cargo_size, self.cargo_max, self.cargo_used = 1, 8, 0
        self.commands = []
        self.__dict__.update(kwargs)
        if "can_attack" not in kwargs:
            self.can_attack = self.ground_weapon or self.air_weapon

    def distance_to(self, other):
        return self.position.distance_to(getattr(other, "position", other))

    def move(self, target):
        self.commands.append(("move", target))

    def __call__(self, ability, target):
        self.commands.append((ability, target))


def mission(style="attack", own=(), enemies=(), structures=()):
    act = ActCombatMission(style, "zone_0", {"marine": 1})
    act.ai = SimpleNamespace(time=0.0, start_location=Point2((0, 0)), bench_combat_tags=set())
    act.ai.units = Units(list(own), act.ai)
    act.ai.enemy_units = Units(list(enemies), act.ai)
    act.ai.enemy_structures = Units(list(structures), act.ai)
    act.roles = SimpleNamespace(is_in_role=lambda role, unit: False, set_tasks=lambda role, units: None)
    act.unit_values = SimpleNamespace(
        power=lambda unit: unit.power,
        real_range=lambda attacker, target: 5 if (attacker.air_weapon if target.is_flying else attacker.ground_weapon) else 0,
    )
    return act


def test_ground_only_enemy_cannot_force_air_harass_retreat():
    banshee = UnitStub(1, UnitTypeId.BANSHEE, flying=True)
    act = mission(own=[banshee], enemies=[UnitStub(2, power=100)])
    assert act._local_power_ratio(act.ai.units, banshee.position) == float("inf")


def test_update_order_keeps_members_and_resets_target_dependent_state():
    act = mission(own=[UnitStub(1)])
    act._bound, act._tags = True, [1]
    act._hold_point = Point2((4, 4))
    act._below_ratio_since = 1
    act.update_order("attack", "zone_10", False, 1)
    assert act._tags == [1] and act._bound
    assert act.style == "attack" and act.zone_id == "zone_10"
    assert act.phase == "fight" and act._hold_point is None and act._below_ratio_since is None
    act.update_order("attack", "zone_10", True, 2)
    assert act.phase == "withdrawing" and act._tags == [1]
    act.update_order("attack", "zone_10", False, 2)  # Unchanged revision cannot reset runtime phase.
    assert act.phase == "withdrawing"


def test_retarget_loaded_transport_preserves_cargo_and_transit():
    medivac = UnitStub(2, UnitTypeId.MEDIVAC, cargo_used=1)
    act = mission(own=[medivac])
    act._bound, act._tags = True, [1, 2]
    act.update_order("attack", "zone_10", False, 1)
    assert act.phase == "transit" and act._tags == [1, 2]


def test_visible_static_antiair_counts_but_snapshot_does_not():
    banshee = UnitStub(1, UnitTypeId.BANSHEE, flying=True, ground_weapon=True)
    turret = UnitStub(2, is_structure=True, air_weapon=True, power=2.0)
    act = mission(own=[banshee], structures=[turret])
    assert act._local_power_ratio(act.ai.units, banshee.position) == pytest.approx(0.5)
    turret.is_snapshot = True
    assert act._local_power_ratio(act.ai.units, banshee.position) == float("inf")


def test_distant_own_units_do_not_inflate_local_power():
    local = UnitStub(1)
    act = mission(own=[local, UnitStub(2, position=(100, 0), power=100)], enemies=[UnitStub(3, power=2)])
    assert act._local_power_ratio(act.ai.units, local.position) == pytest.approx(0.5)


def test_retreat_requires_continuous_two_seconds_and_resets():
    act = mission(own=[UnitStub(1)], enemies=[UnitStub(2, power=2)])
    focus = Point2((0, 0))
    assert not act._maybe_start_withdraw(act.ai.units, focus)
    act.ai.time = 1
    assert not act._maybe_start_withdraw(act.ai.units, focus)
    act.ai.enemy_units[0].power = 0.1
    assert not act._maybe_start_withdraw(act.ai.units, focus)
    act.ai.enemy_units[0].power = 2
    act.ai.time = 2
    assert not act._maybe_start_withdraw(act.ai.units, focus)
    act.ai.time = 4
    assert act._maybe_start_withdraw(act.ai.units, focus)
    assert act.phase == "withdrawing"


def test_defend_does_not_use_task_power_retreat():
    act = mission("defend", own=[UnitStub(1)], enemies=[UnitStub(2, power=100)])
    assert not act._maybe_start_withdraw(act.ai.units, Point2((0, 0)))


@pytest.mark.parametrize("type_id,attrs", [
    (UnitTypeId.RAVEN, {"can_attack": False}),
    (UnitTypeId.DISRUPTOR, {"can_attack": False}),
    (UnitTypeId.LURKERMP, {"can_attack": False}),
    (UnitTypeId.PHOTONCANNON, {"is_powered": False}),
    (UnitTypeId.MARINE, {"health": 0}),
])
def test_spell_ranges_and_inactive_weapons_do_not_force_retreat(type_id, attrs):
    own = UnitStub(1)
    enemy = UnitStub(2, type_id, power=100, **attrs)
    act = mission(own=[own], enemies=[enemy])
    # Sharpy includes spell reach in real_range (e.g. Raven Matrix = 9).
    act.unit_values.real_range = lambda attacker, target: 9
    assert act._local_power_ratio(act.ai.units, own.position) == float("inf")
    assert not act._maybe_start_withdraw(act.ai.units, own.position)
    act.ai.time = 3
    assert not act._maybe_start_withdraw(act.ai.units, own.position)


@pytest.mark.parametrize("type_id,attrs", [
    (UnitTypeId.CARRIER, {"can_attack": False}),
    (UnitTypeId.WIDOWMINEBURROWED, {"can_attack": False}),
    (UnitTypeId.PHOTONCANNON, {"is_powered": True}),
])
def test_active_static_and_automatic_attacks_still_count(type_id, attrs):
    own = UnitStub(1)
    act = mission(own=[own], enemies=[UnitStub(2, type_id, power=2, **attrs)])
    act.unit_values.real_range = lambda attacker, target: 9
    assert act._local_power_ratio(act.ai.units, own.position) == pytest.approx(0.5)


def test_dead_reservations_removed_without_touching_other_missions():
    act = mission(own=[UnitStub(1)])
    act._tags = [1, 2]
    act.ai.bench_combat_tags = {1, 2, 99}
    free, cargo = act._collect_mission_units()
    assert len(free) == 1 and not cargo
    assert act.ai.bench_combat_tags == {1, 99}
    act._release("withdrawn")
    assert act.ai.bench_combat_tags == {99}


def test_loaded_passengers_and_sieged_tanks_keep_identity():
    passenger = UnitStub(2)
    medivac = UnitStub(1, UnitTypeId.MEDIVAC, flying=True, passengers=[passenger], cargo_used=1)
    tank = UnitStub(3, UnitTypeId.SIEGETANKSIEGED)
    act = mission(own=[medivac, tank])
    act.units = {"marine": 1, "medivac": 1, "siege_tank": 1}
    act._tags = [1, 2, 3]
    free, cargo = act._collect_mission_units()
    act._refresh_alive_counts(free, cargo)
    assert act.alive_counts == act.units
    assert act.peak_loaded_units == 1
    assert set(act._tags) == {1, 2, 3}


def test_bind_does_not_fall_back_to_stealing_defenders():
    act = mission(own=[UnitStub(1)])
    act.roles.is_in_role = lambda role, unit: True
    assert not act._bind_units()
    assert not act.ai.bench_combat_tags


def test_load_issues_one_command_per_transport_and_times_out():
    transport = UnitStub(1, UnitTypeId.MEDIVAC, flying=True)
    act = mission(own=[transport, UnitStub(2), UnitStub(3)])
    act._run_load(act.ai.units, Point2((100, 0)))
    assert len(transport.commands) == 1
    act.ai.time = 10
    act._run_load(act.ai.units, Point2((100, 0)))
    assert act.phase == "fight"


def test_full_transport_moves_on_with_ground_leftovers():
    transport = UnitStub(1, UnitTypeId.MEDIVAC, flying=True, cargo_used=8)
    act = mission(own=[transport, UnitStub(2)])
    act._run_load(act.ai.units, Point2((100, 0)))
    assert act.phase == "transit"


def test_failed_unload_withdraws_instead_of_waiting_forever():
    transport = UnitStub(1, UnitTypeId.MEDIVAC, flying=True, cargo_used=1)
    act = mission(own=[transport])
    act._run_unload(act.ai.units, Point2((100, 0)))
    act.ai.time = 10
    act._run_unload(act.ai.units, Point2((100, 0)))
    assert act.phase == "withdrawing"


def test_withdraw_requires_all_survivors_home_and_unloads_before_release():
    near, far = UnitStub(1), UnitStub(2, position=(18, 0))
    act = mission(own=[near, far])
    act.phase = "withdrawing"
    act.combat = SimpleNamespace(add_unit=lambda unit: None, execute=lambda *args: None)
    assert not act._run_withdraw(act.ai.units)  # center is 9, but one unit is far
    transport = UnitStub(3, UnitTypeId.MEDIVAC, flying=True, cargo_used=1)
    act.ai.units = Units([transport], act.ai)
    assert not act._run_withdraw(act.ai.units)
    assert transport.commands


def test_errors_are_isolated_by_task_not_shared_zone():
    tasks = [
        {"task_id": "a", "action": "combat", "target": "zone_1", "_error": "invalid_zone"},
        {"task_id": "b", "action": "combat", "target": "zone_1"},
    ]
    bot = SimpleNamespace(_macro_tasks=tasks)
    assert BenchBot._collect_macro_errors(bot) == {"a": "invalid_zone"}
    backend = SharpyBackend()
    bridge = backend._bridge
    bridge.active_task_ids = ["a", "b"]
    bridge.task_meta = {task_id: ("combat", "zone_1", 1) for task_id in ["a", "b"]}
    bridge.macro_errors = {"a": "invalid_zone"}
    updates = backend.collect_updates()
    assert updates[0].failure_reason == "invalid_zone"
    assert updates[1].failure_reason is None


def test_snapshot_counts_cargo_once_and_keeps_total_assigned_consistent():
    from sc2bench_env.backends.sharpy.state_reader import _add_loaded_passenger_counts
    from sc2bench_env.backends.sharpy.races.terran import TerranAdapter

    passenger = UnitStub(2)
    transport = UnitStub(1, UnitTypeId.MEDIVAC, passengers=[passenger])
    counts = {"medivac": 1}
    _add_loaded_passenger_counts([transport], TerranAdapter(), counts)
    assert counts == {"medivac": 1, "marine": 1}
    counts = {"medivac": 1, "marine": 1}
    _add_loaded_passenger_counts([transport, passenger], TerranAdapter(), counts)
    assert counts == {"medivac": 1, "marine": 1}


def test_removed_mission_releases_its_locks():
    act = mission(own=[UnitStub(1)])
    act._bound = True
    act._tags = [1]
    act.ai.bench_combat_tags = {1}
    bot = SimpleNamespace(_macro_tasks=[{"task_id": "old", "action": "combat", "_act": act}],
                          bridge=SimpleNamespace(get_macro_specs=lambda: []))
    BenchBot._sync_macro_tasks(bot)
    assert not bot._macro_tasks and not act.ai.bench_combat_tags


def test_real_bridge_credits_each_output_once_and_never_rolls_back_on_death():
    from sc2bench_env.backends.base import BackendSnapshot
    from sc2bench_env.backends.sharpy.backend import _Bridge

    bridge = _Bridge()

    def frame(tags, building_tags=None):
        bridge.on_frame(
            snapshot=BackendSnapshot(units={"marine": len(tags)}, info={
                "ready_unit_tags": {"marine": tags},
                "building_entity_tags": building_tags or {},
            }), in_production_units={}, in_progress_buildings={}, in_progress_research={}, macro_errors={},
        )

    frame([100])  # Existing unit at reset is not a new output.
    bridge.active_task_ids = ["first", "second"]
    bridge.task_meta = {name: ("train", "marine", 2) for name in bridge.active_task_ids}
    frame([100, 101])
    assert bridge.peak_ready == {"first": 1, "second": 0}
    frame([102])  # 100/101 died; 102 is nevertheless the second actual output.
    assert bridge.peak_ready == {"first": 2, "second": 0}
    frame([102, 103])
    assert bridge.peak_ready == {"first": 2, "second": 1}
    frame([102, 103, 103])  # Cargo transition/duplicate tag cannot count twice.
    assert bridge.peak_ready == {"first": 2, "second": 1}


def test_real_bridge_multiple_builds_credit_distinct_entities_and_ignore_morphs():
    from sc2bench_env.backends.base import BackendSnapshot
    from sc2bench_env.backends.sharpy.backend import _Bridge

    bridge = _Bridge()

    def frame(buildings):
        bridge.on_frame(snapshot=BackendSnapshot(info={"ready_unit_tags": {}, "building_entity_tags": buildings}),
                        in_production_units={}, in_progress_buildings={}, in_progress_research={}, macro_errors={})

    frame({"command_center": [1]})
    bridge.active_task_ids = ["a", "b"]
    bridge.task_meta = {name: ("build", "barracks", 1) for name in bridge.active_task_ids}
    frame({"orbital_command": [1], "barracks": [2]})
    assert bridge.peak_ready == {"a": 1, "b": 0}
    frame({"orbital_command": [1], "barracks": [3]})
    assert bridge.peak_ready == {"a": 1, "b": 1}


def test_adapter_absolute_targets_do_not_include_later_same_type_orders():
    from sc2bench_env.runtime.task_manager import TaskManager
    from sc2bench_env.backends.base import BackendSnapshot

    manager = TaskManager()
    manager.submit_decision([
        {"action": "train", "target": "marine", "count": 2},
        {"action": "train", "target": "marauder", "count": 1},
        {"action": "train", "target": "marine", "count": 2},
        {"action": "build", "target": "barracks"},
        {"action": "build", "target": "barracks"},
        {"action": "wait"},
    ], game_time=0)
    backend = SharpyBackend()
    from sc2bench_env.backends.sharpy.races.terran import TerranAdapter
    backend._adapter = TerranAdapter()
    backend._bridge.snapshot = BackendSnapshot(units={"marine": 1}, buildings={"barracks": 1})
    backend.submit(manager.active_demands())
    assert [row["to_count"] for row in backend._bridge.macro_specs] == [3, 1, 5, 2, 3]


def test_completed_training_act_stops_before_next_model_decision():
    spec = {"task_id": "done", "action": "train"}
    bot = SimpleNamespace(_macro_tasks=[{"task_id": "done", "action": "train"}], bridge=SimpleNamespace(
        get_macro_specs=lambda: [spec], task_meta={"done": ("train", "marine", 2)}, peak_ready={"done": 2},
    ))
    BenchBot._sync_macro_tasks(bot)
    assert not bot._macro_tasks


@pytest.mark.parametrize("action", ["train", "build"])
def test_queue_occupancy_keeps_submission_order_after_inventory_losses(action):
    from sc2bench_env.backends.base import BackendSnapshot

    backend = SharpyBackend()
    bridge = backend._bridge
    # submit() publishes this list in order_index order. A new demand after
    # casualties has a lower living baseline, but must not jump the old demand.
    bridge.active_task_ids = ["old", "new"]
    bridge.baselines = {"old": 10, "new": 0}
    target = "marine" if action == "train" else "barracks"
    bridge.task_meta = {name: (action, target, 1) for name in bridge.active_task_ids}
    bridge.snapshot = BackendSnapshot(info={"workers_en_route": {target: 1}})
    bridge.in_production_units = {target: 1}
    updates = backend.collect_updates()
    assert [update.demand_id for update in updates] == ["old", "new"]
    if action == "train":
        assert [update.in_progress for update in updates] == [1, 0]
    else:
        from sc2bench_env.runtime.task import DemandState
        assert updates[0].state == DemandState.WORKER_EN_ROUTE
        assert updates[1].state == DemandState.WAITING_TO_START


@pytest.mark.parametrize("reason", [None, "resources", "prerequisite:barracks"])
def test_other_building_under_construction_does_not_invent_resource_reason(reason):
    from sc2bench_env.backends.base import BackendSnapshot

    backend = SharpyBackend()
    bridge = backend._bridge
    bridge.active_task_ids = ["unstarted"]
    bridge.task_meta = {"unstarted": ("build", "factory", 1)}
    bridge.snapshot = BackendSnapshot(minerals=1000, vespene=1000,
                                      info={"under_construction": {"factory": 1}})
    bridge.waiting_reasons = {"unstarted": reason} if reason else {}
    assert backend.collect_updates()[0].waiting_for == reason


def test_cancel_preserves_exact_paid_slots_even_before_first_output():
    from sc2bench_env.runtime.task_manager import TaskManager, DemandUpdate
    from sc2bench_env.runtime.task import DemandState

    manager = TaskManager()
    manager.submit_decision([{"action": "train", "target": "marine", "count": 5}, {"action": "wait"}], game_time=0)
    demand = manager.active_demands()[0]
    manager.apply_updates([DemandUpdate(demand_id=demand.demand_id, state=DemandState.IN_PRODUCTION, in_progress=2)], game_time=1)
    assert manager.training_summary({"marine": 2})["marine"]["waiting_to_produce"] == 3
    manager.submit_decision([{"action": "cancel", "target_action": "train", "target": "marine"}, {"action": "wait"}], game_time=2)
    assert demand.count == 2 and demand.produced == 0


def test_real_bridge_queue_occupancy_is_not_duplicated_between_orders():
    backend = SharpyBackend()
    bridge = backend._bridge
    bridge.active_task_ids = ["a", "b"]
    bridge.task_meta = {name: ("train", "marine", 2) for name in ["a", "b"]}
    bridge.in_production_units = {"marine": 1}
    updates = backend.collect_updates()
    assert [update.in_progress for update in updates] == [1, 0]


def test_missing_production_prerequisite_is_visible_in_training():
    from sc2bench_env.backends.sharpy.macro import ActOngoingMacroTasks
    from sc2bench_env.runtime.task_manager import TaskManager, DemandUpdate
    from sc2bench_env.runtime.task import DemandState

    macro = ActOngoingMacroTasks([])
    macro._count_ready = lambda name: 0 if name == "starport" else 1
    task = {"action": "train", "target": "banshee"}
    assert macro._prereq_blocked(task)
    assert task["_waiting_for"] == "prerequisite:starport"
    manager = TaskManager()
    manager.submit_decision([{"action": "train", "target": "banshee", "count": 1}, {"action": "wait"}], game_time=0)
    demand = manager.active_demands()[0]
    manager.apply_updates([DemandUpdate(demand_id=demand.demand_id, state=DemandState.WAITING_TO_START,
                                       waiting_for=task["_waiting_for"], in_progress=0)], game_time=1)
    assert manager.training_summary({})["banshee"]["waiting_for"] == "prerequisite:starport"


def test_drop_target_at_home_does_not_divide_by_zero():
    transport = UnitStub(1, UnitTypeId.MEDIVAC, flying=True, cargo_used=1)
    act = mission(own=[transport])
    act._run_transit(act.ai.units, Point2((0, 0)))
    act._run_unload(act.ai.units, Point2((0, 0)))
    assert transport.commands


def test_auto_defense_occupancy_is_not_reported_as_free_army():
    from sc2bench_env import Environment
    from sc2bench_env.backends.base import BackendSnapshot
    from sc2bench_env.backends.fake import FakeBackend

    env = Environment(FakeBackend())
    snapshot = BackendSnapshot(units={"marine": 3}, info={"unavailable_army": {"marine": 2}})
    view = env._own_forces_view(snapshot)
    assert view.assigned == {"marine": 2}
    assert view.free == {"marine": 1}
    assert env._idle_army_counts(snapshot) == {"marine": 1}


def test_assigned_never_exceeds_latest_total_on_a_casualty_frame():
    from sc2bench_env.interface.observations import split_own_forces

    view = split_own_forces({"marine": 2}, assigned={"marine": 3})
    assert view.assigned == {"marine": 2}
    assert not view.free
