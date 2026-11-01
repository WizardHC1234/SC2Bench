"""Supply Drop, Nexus recall, Generate Creep and version ability checks."""
from types import SimpleNamespace

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.position import Point2

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.backends.sharpy.protoss_recall import (
    select_main_nexus, split_by_radius,
)
from sc2bench_env.backends.sharpy.zerg_creep import maintain_station_creep
from sc2bench_env.catalog.knowledge import food_provided, game_data, require_snapshot_match, snapshot_has_ability
from sc2bench_env.interface.actions import ActionValidationError, parse_game_action
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.drive import drive
from tests.unit.test_combat_execution import UnitStub


def _wait(seconds=5):
    return {"name": "advance", "arguments": {"seconds": seconds}}


def test_supply_drop_spends_shared_energy_and_adds_version_supply():
    backend = FakeBackend()
    env = Environment(backend, record_trajectory=False)
    env.reset(EpisodeConfig())
    backend.buildings = {"command_center": 0, "orbital_command": 1, "supply_depot": 1}
    backend.structure_types = {"townhall_0": "orbital_command"}
    backend.orbital_energy = 50.0
    before_cap = backend.supply_cap
    grant = food_provided("terran", "supply_depot")
    obs, feedback, _, _ = drive(env, [{"name": "supply_drop", "arguments": {}}, _wait(5)])
    assert feedback.receipts[0].result == "accepted"
    for _ in range(4):
        obs, _, _, _ = drive(env, [_wait(2)])
        if not any(d.action == "supply_drop" and d.is_active for d in env.task_manager.demands.values()):
            break
    assert not any(d.action == "supply_drop" and d.is_active for d in env.task_manager.demands.values())
    assert backend.supply_cap == before_cap + grant
    assert backend.orbital_energy < 50
    _, feedback, _, _ = drive(env, [{"name": "supply_drop", "arguments": {}}, _wait(1)])
    failed = [d for d in env.task_manager.demands.values() if d.action == "supply_drop" and d.state.value == "failed"]
    assert failed and failed[-1].failure_reason == "no_legal_depot"
    env.close()


def test_recall_is_protoss_only_and_does_not_start_a_move_retreat():
    with_action = parse_game_action(
        {"name": "retreat", "arguments": {"group": "group_1", "method": "recall"}}, race="protoss",
    )
    assert with_action.method == "recall"
    omitted = parse_game_action({"name": "retreat", "arguments": {"group": "group_1"}}, race="protoss")
    assert omitted.method is None
    try:
        parse_game_action(
            {"name": "retreat", "arguments": {"group": "group_1", "method": "recall"}}, race="terran",
        )
    except ActionValidationError as exc:
        assert "protoss" in str(exc)
    else:
        raise AssertionError("terran recall was accepted")

    env = Environment(FakeBackend(), record_trajectory=False)
    env.reset(EpisodeConfig(race="protoss"))
    env.backend.units["zealot"] = 4
    drive(env, [{"name": "combat", "arguments": {"style": "attack", "target": "zone_3", "units": {"zealot": 2}}}, _wait(1)])
    _, feedback, _, _ = drive(env, [
        {"name": "retreat", "arguments": {"group": "group_1", "method": "recall"}}, _wait(1),
    ])
    assert feedback.receipts[0].result == "accepted"
    demand = next(d for d in env.task_manager.demands.values() if d.group == "group_1")
    assert demand.retreat_method == "recall"
    assert demand.recall_status == "pending"
    assert demand.withdrawing is False
    env.close()


def test_recall_uses_only_the_main_nexus_and_splits_the_radius():
    start = Point2((0, 0))
    main = UnitStub(1, UnitTypeId.NEXUS, position=(2, 0), is_ready=True, health=1000)
    natural = UnitStub(2, UnitTypeId.NEXUS, position=(40, 0), is_ready=True, health=1000)
    unfinished = UnitStub(3, UnitTypeId.NEXUS, position=(1, 0), is_ready=False, health=100)
    assert select_main_nexus([natural], start) == (None, "nexus_missing")
    assert select_main_nexus([unfinished], start)[1] == "nexus_incomplete"
    chosen, reason = select_main_nexus([main, natural], start)
    assert chosen is main and reason is None
    near = UnitStub(4, position=(0, 0))
    far = UnitStub(5, position=(10, 0))
    inside, outside = split_by_radius([near, far], Point2((0, 0)))
    assert inside == [near] and outside == [far]


def test_generate_creep_holds_only_when_stationary_and_turns_off_before_moving():
    overlord = UnitStub(1, UnitTypeId.OVERLORD, position=(0, 0))
    overlord.commands = []
    ai = SimpleNamespace()
    assert maintain_station_creep(ai, overlord, True) == "toggled"
    assert overlord.commands[-1][0] == AbilityId.BEHAVIOR_GENERATECREEPON
    assert maintain_station_creep(ai, overlord, True) == "holding"
    assert maintain_station_creep(ai, overlord, False) == "toggled"
    assert overlord.commands[-1][0] == AbilityId.BEHAVIOR_GENERATECREEPOFF
    drone = UnitStub(2, UnitTypeId.DRONE)
    drone.commands = []
    assert maintain_station_creep(ai, drone, True) == "clear"
    assert drone.commands == []


def test_both_target_versions_expose_the_special_abilities():
    windows = require_snapshot_match(
        game_version="5.0.16.97563",
        data_version="F364D7C8BB1A0444ABC9BEE547B3FBB3",
        base_build=97563,
    )
    linux = require_snapshot_match(
        game_version="4.10.0.75689",
        data_version="B89B5D6FA7CBF6452E721311BFBC6CB2",
        base_build=75689,
    )
    for snapshot in (windows, linux):
        with game_data(snapshot):
            for link in ("SupplyDrop", "NexusMassRecall", "GenerateCreep"):
                assert snapshot_has_ability(link)
            assert food_provided("terran", "supply_depot") > 0
