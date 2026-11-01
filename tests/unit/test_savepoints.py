"""Sequential savepoints on FakeBackend, plus the Sharpy bookmark handshake."""
from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.base import BackendSnapshot
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.backends.sharpy.backend import SharpyBackend, _Bridge
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.runtime.savepoint import SavepointError
from tests.helpers.drive import drive


def wait(seconds=1):
    return {"name": "advance", "arguments": {"seconds": seconds}}


def train(target, count=1):
    return {"name": "train", "arguments": {"target": target, "count": count}}


def build(target):
    return {"name": "build", "arguments": {"target": target}}


def line_signature(env):
    """Comparable game and task state. Demand ids are allowed to differ."""
    snapshot = env.backend.snapshot()
    demands = tuple(
        (demand.action, demand.target, demand.count, demand.produced, str(demand.state))
        for demand in env.task_manager.demands.values()
    )
    return (
        snapshot.minerals,
        snapshot.vespene,
        round(snapshot.game_time_seconds, 3),
        tuple(sorted(snapshot.units.items())),
        tuple(sorted(snapshot.buildings.items())),
        tuple(sorted((snapshot.info.get("under_construction") or {}).items())),
        demands,
    )


@pytest.fixture
def game(tmp_path):
    backend = FakeBackend(mineral_income_per_second=10, vespene_income_per_second=0)
    env = Environment(backend, record_trajectory=True, record_dir=tmp_path)
    env.reset(EpisodeConfig(game_time_limit_seconds=600))
    backend.minerals = 10000
    backend.supply_cap = 200
    backend.buildings["barracks"] = 1
    try:
        yield env, backend
    finally:
        env.close()


def test_branches_keep_separate_tasks_time_and_records(game):
    env, backend = game
    drive(env, [wait(5)])
    save = env.create_savepoint(label="five_seconds")
    minerals = backend.minerals

    drive(env, [train("marine"), wait(1)])
    assert any(demand.action == "train" for demand in env.task_manager.demands.values())
    assert backend.game_time_seconds > save.base_game_time_seconds
    trunk_session = (env.record_path / "session.json").read_text(encoding="utf-8")

    env.restore_savepoint(save, branch_id="economic")
    assert backend.minerals == minerals
    assert backend.game_time_seconds == pytest.approx(save.base_game_time_seconds)
    assert not any(demand.action == "train" for demand in env.task_manager.demands.values())
    assert (env.record_path / "session.json").read_text(encoding="utf-8") == trunk_session
    branch = env.record_path / "branches" / "economic"
    assert (branch / "branch.json").is_file()
    assert (branch / "session.json").is_file()
    assert '"decision_index": 1' in (branch / "session.json").read_text(encoding="utf-8")

    def aggressive(current):
        drive(current, [train("marine"), wait(1)])
        return current.backend.snapshot().game_time_seconds

    branched_time = env.run_branch(save, branch_id="aggressive", policy=aggressive)
    assert branched_time == pytest.approx(save.base_game_time_seconds + 1)
    assert (env.record_path / "session.json").read_text(encoding="utf-8") == trunk_session
    aggressive_session = (env.record_path / "branches" / "aggressive" / "session.json").read_text(
        encoding="utf-8"
    )
    assert '"decision_index": 2' in aggressive_session
    assert aggressive_session != (branch / "session.json").read_text(encoding="utf-8")


def test_the_exploration_root_stays_until_it_is_replaced(game):
    env, _backend = game
    drive(env, [wait(1)])
    first = env.create_savepoint(label="first", context={"note": "fork"})
    drive(env, [wait(1)])
    with pytest.raises(SavepointError, match="replace=True"):
        env.create_savepoint(label="second")
    assert env.saved_context(first) == {"note": "fork"}
    env.saved_context(first)["note"] = "changed"
    assert env.saved_context(first) == {"note": "fork"}
    second = env.create_savepoint(label="second", replace=True)
    with pytest.raises(SavepointError):
        env.restore_savepoint(first, branch_id="stale")
    env.restore_savepoint(second, branch_id="current")
    assert env.backend.game_time_seconds == pytest.approx(second.base_game_time_seconds)


def test_pending_release_and_open_tool_turn_are_rejected(game):
    env, _backend = game
    env._pending_release = {"started": 0}
    with pytest.raises(SavepointError, match="in progress"):
        env.create_savepoint()
    env._pending_release = None
    env.begin_tool_turn()
    with pytest.raises(SavepointError, match="tool turn"):
        env.create_savepoint()


def test_non_blocking_games_cannot_bookmark(tmp_path):
    backend = FakeBackend()
    env = Environment(backend, record_trajectory=False)
    env.reset(EpisodeConfig(blocking_decisions=False))
    try:
        with pytest.raises(SavepointError, match="paused blocking"):
            env.create_savepoint()
    finally:
        env.close()


def test_restore_clears_a_branch_ending(game):
    env, backend = game
    drive(env, [wait(2)])
    save = env.create_savepoint()
    backend.terminated = True
    backend.result = "Result.Victory"
    backend.end_reason = "test_end"
    env.restore_savepoint(save, branch_id="again")
    assert backend.terminated is False
    assert backend.result is None
    assert backend.game_time_seconds == pytest.approx(2)


def test_control_request_pauses_again_without_a_game_step():
    bridge = _Bridge()
    bridge.ready.set()
    bridge.advance_allowed.clear()
    bridge.blocking_decisions = True
    seen = []

    def bot():
        while True:
            bridge.wait_for_decision()
            request = bridge.take_control_request()
            if request is None:
                seen.append("release")
                return
            seen.append(request.operation)
            bridge.finish_control(request)

    thread = threading.Thread(target=bot)
    thread.start()
    try:
        token = bridge.post_control("quick_save", logical_time_seconds=12.0, timeout=2)
        assert token == "1"
        assert bridge.saved_logical_time == 12.0
        assert not bridge.advance_allowed.is_set()
        bridge.advance_allowed.set()
        thread.join(timeout=2)
    finally:
        bridge.advance_allowed.set()
        thread.join(timeout=2)
    assert seen == ["quick_save", "release"]
    assert not thread.is_alive()


def test_explore_returns_every_branch_to_the_same_moment(game):
    env, backend = game
    drive(env, [wait(4)])
    save = env.create_savepoint(label="fork")
    seen = []

    def remember(name):
        def policy(current):
            seen.append((name, current.backend.game_time_seconds))
            drive(current, [train("marine"), wait(3)])
            if name == "fails":
                raise RuntimeError("branch failed")
            return current.backend.snapshot().units.get("marine", 0)
        return policy

    results = env.explore(save, {
        "fails": remember("fails"),
        "holds": remember("holds"),
    })
    assert [item.branch_id for item in results] == ["fails", "holds"]
    assert isinstance(results[0].error, RuntimeError)
    assert results[1].error is None
    assert seen == [("fails", pytest.approx(4)), ("holds", pytest.approx(4))]
    assert backend.game_time_seconds == pytest.approx(4)
    assert not any(demand.action == "train" for demand in env.task_manager.demands.values())
    with pytest.raises(SavepointError, match="parked"):
        drive(env, [wait(1)])
    env.run_branch(save, branch_id="chosen", policy=lambda current: drive(current, [wait(1)]))
    assert backend.game_time_seconds == pytest.approx(5)


def test_explore_keeps_finished_branches_when_the_game_cannot_park(game):
    env, backend = game
    drive(env, [wait(2)])
    save = env.create_savepoint(label="park")

    def played(current):
        backend._engine_bookmark = "gone"
        return "played"

    with pytest.raises(SavepointError, match="could not return") as caught:
        env.explore(save, {"only": played})
    assert caught.value.results[0].branch_id == "only"
    assert caught.value.results[0].value == "played"
    assert caught.value.results[0].error is None


def test_task_manager_round_trip_drops_later_demands(game):
    env, _backend = game
    drive(env, [train("marine"), wait(1)])
    saved = env.task_manager.export_state()
    drive(env, [train("marine", 4), wait(1)])
    assert sum(demand.count for demand in env.task_manager.demands.values()) == 5
    env.task_manager.import_state(saved)
    demands = list(env.task_manager.demands.values())
    assert [(demand.target, demand.count) for demand in demands] == [("marine", 1)]


def test_same_actions_from_one_savepoint_match_and_keep_unfinished_work(game):
    env, backend = game
    drive(env, [build("supply_depot"), wait(5)])
    save = env.create_savepoint(label="depot_started")
    started = line_signature(env)
    assert backend.buildings.get("supply_depot", 0) == 0

    def play(current):
        drive(current, [wait(30)])
        drive(current, [train("marine"), wait(20)])
        return line_signature(current)

    first = env.run_branch(save, branch_id="repeat_a", policy=play)
    second = env.run_branch(save, branch_id="repeat_b", policy=play)
    assert first == second
    assert first[2] > started[2]
    assert env.restore_savepoint(save, branch_id="back").game_time_seconds == pytest.approx(5)
    assert line_signature(env) == started
    metadata = json.loads((env.record_path / "branches" / "back" / "branch.json").read_text(encoding="utf-8"))
    assert metadata["parent_episode_id"] == save.episode_id
    assert metadata["label"] == "depot_started"
    assert metadata["generation"] == save.generation
    assert metadata["engine_state_hash"] == save.engine_state_hash


def test_action_ids_from_a_discarded_branch_can_be_used_again(game):
    env, _backend = game
    drive(env, [train("marine"), wait(1)], retry_ids=["kept"])
    save = env.create_savepoint()
    drive(env, [train("marine"), wait(1)], retry_ids=["branch_only"])
    env.restore_savepoint(save, branch_id="retry")
    _, repeated, _, _ = drive(env, [train("marine"), wait(1)], retry_ids=["branch_only"])
    _, duplicate, _, _ = drive(env, [train("marine"), wait(1)], retry_ids=["kept"])
    assert repeated.receipts[0].result == "accepted"
    assert duplicate.receipts[0].result == "ignored_duplicate_action_id"


def _restore_in_fresh_process(payload: dict) -> str:
    from pathlib import Path

    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig
    from sc2bench_env.runtime.savepoint import SavepointError, SavepointHandle

    env = Environment("fake", record_dir=Path(payload["record_dir"]))
    env.reset(EpisodeConfig(game_time_limit_seconds=30))
    handle = SavepointHandle(**payload["handle"])
    try:
        env.restore_savepoint(handle, branch_id="foreign")
    except SavepointError as exc:
        return str(exc)
    finally:
        env.close()
    return "restored"


def test_a_savepoint_does_not_restore_in_another_process(game, tmp_path):
    import multiprocessing

    env, _backend = game
    drive(env, [wait(1)])
    save = env.create_savepoint(label="local")
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(1) as pool:
        message = pool.apply(
            _restore_in_fresh_process,
            ({
                "record_dir": str(tmp_path / "other"),
                "handle": {
                    "episode_id": save.episode_id,
                    "generation": save.generation,
                    "label": save.label,
                    "base_game_time_seconds": save.base_game_time_seconds,
                    "engine_state_hash": save.engine_state_hash,
                },
            },),
        )
    assert message != "restored"
    assert "no longer valid" in message


def test_versus_sides_cannot_bookmark(tmp_path):
    from sc2bench_env.versus.match import VersusMatch

    match = VersusMatch(backend="fake", record_trajectory=False, record_dir=tmp_path)
    match.reset(EpisodeConfig(race="terran", enemy_race="zerg", game_time_limit_seconds=60))
    try:
        with pytest.raises(SavepointError, match="cannot bookmark"):
            match._sides[0].create_savepoint()
    finally:
        match.close()


def test_sharpy_versus_side_cannot_bookmark():
    backend = SharpyBackend()
    backend.prepare_for_versus(EpisodeConfig(race="terran", enemy_race="zerg"))
    assert backend.supports_savepoints() is False
    with pytest.raises(SavepointError, match="paused blocking"):
        backend.create_engine_bookmark()


def test_handles_do_not_cross_episodes_or_record_without_a_branch(game):
    env, backend = game
    drive(env, [wait(1)])
    save = env.create_savepoint()
    with pytest.raises(SavepointError, match="branch_id"):
        env.restore_savepoint(save)
    backend.terminated = True
    with pytest.raises(SavepointError, match="already ended"):
        env.create_savepoint(replace=True)
    backend.terminated = False
    env.reset(EpisodeConfig(game_time_limit_seconds=600))
    with pytest.raises(SavepointError, match="no longer valid"):
        env.restore_savepoint(save, branch_id="other_episode")
    env.reset(EpisodeConfig(realtime=True))
    with pytest.raises(SavepointError, match="paused blocking"):
        env.create_savepoint()


def test_explore_keeps_the_root_and_leaves_the_trunk_unchanged(game):
    env, _backend = game
    drive(env, [wait(3)])
    save = env.create_savepoint(label="root", context={"turn": 1})
    drive(env, [train("marine"), wait(1)])
    trunk = (env.record_path / "session.json").read_text(encoding="utf-8")

    def move_root(current):
        current.create_savepoint(label="nested", replace=True)

    results = env.explore(save, {"nested": move_root, "quiet": lambda current: current.backend.game_time_seconds})
    assert isinstance(results[0].error, SavepointError)
    assert results[1].value == pytest.approx(3)
    assert env.saved_context(save) == {"turn": 1}
    assert (env.record_path / "session.json").read_text(encoding="utf-8") == trunk
    env.close()
    assert (env.record_path / "session.json").read_text(encoding="utf-8") == trunk


def test_uncopyable_context_and_unsupported_backend_are_rejected(game):
    env, backend = game
    with pytest.raises(SavepointError, match="copyable"):
        env.create_savepoint(context=threading.Lock())
    backend.supports_savepoints = lambda: False
    with pytest.raises(SavepointError, match="cannot bookmark"):
        env.create_savepoint()


def test_control_errors_and_logical_time_stay_on_the_bridge():
    bridge = _Bridge()
    bridge.ready.set()
    bridge.advance_allowed.clear()
    bridge.blocking_decisions = True
    with pytest.raises(SavepointError, match="in time"):
        bridge.post_control("quick_save", logical_time_seconds=1, timeout=0.05)
    assert bridge.control_request is None
    assert not bridge.advance_allowed.is_set()

    def fail_once():
        bridge.wait_for_decision()
        request = bridge.take_control_request()
        bridge.fail_control(request, RuntimeError("load failed"))

    thread = threading.Thread(target=fail_once)
    thread.start()
    try:
        with pytest.raises(SavepointError, match="load failed"):
            bridge.post_control("quick_load", logical_time_seconds=4, timeout=2)
    finally:
        bridge.advance_allowed.set()
        thread.join(timeout=2)
    assert not thread.is_alive()

    bridge.time_offset_seconds = 600
    bridge.max_game_time = 600
    # The bot clock already includes the offset before on_frame sees the snapshot.
    snapshot = BackendSnapshot(game_time_seconds=601.5, minerals=40)
    bridge.on_frame(
        snapshot=snapshot,
        in_production_units={},
        in_progress_buildings={},
        in_progress_research={},
        macro_errors={},
    )
    assert bridge.snapshot.game_time_seconds == pytest.approx(601.5)
    assert bridge.snapshot.terminated is True
    assert bridge.snapshot.end_reason == "time_limit"
    assert bridge.leave_requested is True
    assert bridge.advance_allowed.is_set()


def test_time_limit_with_a_bookmark_stays_in_the_match():
    bridge = _Bridge()
    bridge.ready.set()
    bridge.blocking_decisions = True
    bridge.max_game_time = 10
    bridge.engine_bookmark = "1"
    bridge.advance_allowed.set()
    snapshot = BackendSnapshot(game_time_seconds=10, minerals=50)
    paused = bridge.on_frame(
        snapshot=snapshot,
        in_production_units={},
        in_progress_buildings={},
        in_progress_research={},
        macro_errors={},
    )
    assert paused is True
    assert bridge.snapshot.end_reason == "time_limit"
    assert bridge.leave_requested is False
    assert not bridge.advance_allowed.is_set()

    def allow_load():
        bridge.wait_for_decision()
        request = bridge.take_control_request()
        assert request.operation == "quick_load"
        with bridge.lock:
            bridge.snapshot.terminated = False
            bridge.snapshot.end_reason = None
        bridge.finish_control(request)

    thread = threading.Thread(target=allow_load)
    thread.start()
    try:
        bridge.post_control("quick_load", logical_time_seconds=4, timeout=2)
    finally:
        bridge.advance_allowed.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert bridge.snapshot.terminated is False


def test_ability_clock_returns_to_the_save_after_a_branch():
    from sc2bench_env.backends.sharpy.bot import BenchBot

    bot = object.__new__(BenchBot)
    clock = {7: {"stim": 40.0}}
    bot.knowledge = SimpleNamespace(cooldown_manager=SimpleNamespace(used_dict=clock))
    BenchBot._remember_ability_clock(bot)
    bot.knowledge.cooldown_manager.used_dict[7]["stim"] = 80.0
    BenchBot._restore_ability_clock(bot)
    assert bot.knowledge.cooldown_manager.used_dict == {7: {"stim": 40.0}}
    bot.knowledge.cooldown_manager.used_dict[7]["stim"] = 90.0
    assert bot._saved_ability_clock == {7: {"stim": 40.0}}


def test_home_rally_returns_to_the_save_after_a_branch():
    from sc2bench_env.backends.sharpy.bot import BenchBot

    bot = object.__new__(BenchBot)
    gather = SimpleNamespace(
        _rallied_tags={1, 2},
        _last_point=SimpleNamespace(x=3.0, y=4.0),
    )
    bot.bench_home_gather = gather
    BenchBot._remember_home_gather(bot)
    gather._rallied_tags.add(9)
    gather._last_point = SimpleNamespace(x=8.0, y=8.0)
    BenchBot._restore_home_gather(bot)
    assert gather._rallied_tags == {1, 2}
    assert (gather._last_point.x, gather._last_point.y) == (3.0, 4.0)


def test_home_micro_returns_to_the_save_after_a_branch():
    from sc2bench_env.backends.sharpy.bot import BenchBot

    cyclone = SimpleNamespace(locks={1: (8, 20.0)}, pending_locks={1: (8, 12.0)})
    raven = SimpleNamespace(turret_points={3: (1.0, 2.0)}, prepared_at=11.0, claimed_until={9: 30.0})
    tank = SimpleNamespace(siege_status={4: SimpleNamespace(requested_time=5.0, status=1)})
    rules = SimpleNamespace(generic_micro=None, unit_micros={"cyclone": cyclone, "raven": raven, "tank": tank})
    bot = object.__new__(BenchBot)
    bot.bench_home_gather = SimpleNamespace(_micro_rules=rules)
    BenchBot._remember_home_micro(bot)
    cyclone.locks[1] = (99, 1.0)
    cyclone.pending_locks.clear()
    raven.claimed_until[9] = 80.0
    raven.prepared_at = 50.0
    tank.siege_status[4].requested_time = 90.0
    BenchBot._restore_home_micro(bot)
    assert cyclone.locks == {1: (8, 20.0)}
    assert cyclone.pending_locks == {1: (8, 12.0)}
    assert raven.claimed_until == {9: 30.0}
    assert raven.prepared_at == 11.0
    assert raven.turret_points == {3: (1.0, 2.0)}
    assert tank.siege_status[4].requested_time == 5.0
    cyclone.locks[2] = (7, 1.0)
    assert 2 not in bot._saved_home_micro[0]["locks"]


def test_saved_sightings_survive_a_branch():
    from sc2bench_env.backends.sharpy.bot import BenchBot

    bot = object.__new__(BenchBot)
    bot.enemy_units_manager = SimpleNamespace(
        _known_enemy_units_dict={"marine": {4, 5}},
        _morphed_type={"cc": 1},
        _enemy_cloak_trigger=True,
    )
    dead = SimpleNamespace(tag=4)
    bot.lost_units_manager = SimpleNamespace(
        _my_lost_units={"scv": [dead]},
        _enemy_lost_units={"marine": []},
        _cancelled_tags={3},
        _cancel_commanded_tags={6},
    )
    BenchBot._remember_sightings(bot)
    bot.enemy_units_manager._known_enemy_units_dict = {"zergling": {1}}
    bot.enemy_units_manager._morphed_type = {}
    bot.enemy_units_manager._enemy_cloak_trigger = False
    bot.lost_units_manager._my_lost_units = {}
    bot.lost_units_manager._cancelled_tags = set()
    assert BenchBot._restore_sightings(bot) is True
    assert bot.enemy_units_manager._known_enemy_units_dict == {"marine": {4, 5}}
    assert bot.enemy_units_manager._morphed_type == {"cc": 1}
    assert bot.enemy_units_manager._enemy_cloak_trigger is True
    assert bot.lost_units_manager._my_lost_units["scv"][0].tag == 4
    assert bot.lost_units_manager._cancelled_tags == {3}
    bot.enemy_units_manager._known_enemy_units_dict["marine"].add(8)
    assert 8 not in bot._saved_sightings["known"]["marine"]


def test_bot_clock_keeps_logical_time_after_the_raw_loop_resets():
    from sc2bench_env.backends.sharpy.bot import BenchBot

    bot = object.__new__(BenchBot)
    bot.state = SimpleNamespace(game_loop=0)
    bot.bridge = SimpleNamespace(time_offset_seconds=48.0)
    assert bot.time == pytest.approx(48.0)
    bot.state = SimpleNamespace(game_loop=224)
    assert bot.time == pytest.approx(58.0)


def test_quick_load_realigns_managers_before_acts_run():
    from sc2bench_env.backends.sharpy.bot import BenchBot
    from sharpy.managers.extensions.custom_func_manager import CustomFuncManager

    class Manager:
        def __init__(self):
            self.updates = 0

        async def update(self):
            self.updates += 1

    cache = Manager()
    roles_manager = Manager()
    hook = CustomFuncManager(lambda: None)
    acts = Manager()
    cleared = []
    unit = SimpleNamespace(tag=7)
    bot = object.__new__(BenchBot)
    bot.knowledge = SimpleNamespace(
        managers=[cache, roles_manager, hook, acts],
        reserved_minerals=40,
        reserved_gas=10,
    )
    bot.roles = SimpleNamespace(
        had_task_set={1, 2},
        roles=[SimpleNamespace(clear=lambda: cleared.append("role"))],
    )
    bot.enemy_units_manager = SimpleNamespace(
        _known_enemy_units_dict={"scv": {9}},
        _morphed_type={"cc": 1},
    )
    bot.lost_units_manager = SimpleNamespace(
        _my_lost_units={"scv": [1]},
        _enemy_lost_units={"marine": [2]},
        _cancelled_tags={3},
        _cancel_commanded_tags={4},
    )
    bot.units = [unit]
    bot.structures = []
    bot.enemy_units = []
    bot.enemy_structures = []
    bot.all_units = [unit]
    bot.previous_units_manager = SimpleNamespace(previous_units={99: unit})

    asyncio.run(BenchBot._realign_after_quick_load(bot))

    assert bot._drop_actions_after_load is True
    bot.actions = ["train"]
    bot.unit_tags_received_action = {7}
    issued = SimpleNamespace(actions=["old"], block_list=["blocked"])
    bot.knowledge.action_manager = issued
    BenchBot._hold_load_frame_actions(bot)
    assert bot.actions == []
    assert bot.unit_tags_received_action == set()
    assert issued.actions == []
    assert issued.block_list == []
    assert bot._drop_actions_after_load is False
    assert cache.updates == 1
    assert roles_manager.updates == 1
    assert acts.updates == 0
    assert bot.knowledge.reserved_minerals == 0
    assert bot.knowledge.reserved_gas == 0
    assert bot.roles.had_task_set == set()
    assert cleared == ["role"]
    assert bot.enemy_units_manager._known_enemy_units_dict == {}
    assert bot.enemy_units_manager._morphed_type == {}
    assert bot.lost_units_manager._my_lost_units == {}
    assert bot.lost_units_manager._enemy_lost_units == {}
    assert bot.previous_units_manager.previous_units == {7: unit}
    assert bot._units_previous_map == {7: unit}


def test_bookmark_frame_still_defers_commands_without_managers():
    from sc2bench_env.backends.sharpy.bot import BenchBot

    bot = object.__new__(BenchBot)
    bot.knowledge = None
    bot._drop_actions_after_load = False
    asyncio.run(BenchBot._realign_after_quick_load(bot))
    assert bot._drop_actions_after_load is True
    bot.actions = ["mine"]
    bot.unit_tags_received_action = {1}
    BenchBot._hold_load_frame_actions(bot)
    assert bot.actions == []
    assert bot._drop_actions_after_load is False


def test_simulation_fingerprint_tracks_position_health_and_orders():
    from s2clientprotocol import raw_pb2, sc2api_pb2

    from sc2bench_env.backends.sharpy.engine_fingerprint import (
        describe_fingerprint_diff,
        fingerprint_observation,
    )

    def observation(x: float, health: float, ability: int):
        message = sc2api_pb2.Observation()
        message.game_loop = 999
        message.player_common.minerals = 50
        unit = message.raw_data.units.add()
        unit.tag = 7
        unit.unit_type = 48
        unit.alliance = raw_pb2.Self
        unit.pos.x = x
        unit.pos.y = 12.5
        unit.pos.z = 0.0
        unit.facing = 1.25
        unit.health = health
        unit.health_max = 45
        unit.is_selected = True
        unit.is_on_screen = True
        order = unit.orders.add()
        order.ability_id = ability
        order.target_world_space_pos.x = 20
        order.target_world_space_pos.y = 30
        message.raw_data.map_state.visibility.size.x = 4
        message.raw_data.map_state.visibility.size.y = 4
        message.raw_data.map_state.visibility.data = b"\x02\x02\x02\x02"
        message.score.score = 10
        message.score.score_details.current_apm = 400
        return message

    saved = fingerprint_observation(observation(3.5, 45, 16))
    selected_again = fingerprint_observation(observation(3.5, 45, 16))
    selected_again_observation = observation(3.5, 45, 16)
    selected_again_observation.game_loop = 0
    reloaded = fingerprint_observation(selected_again_observation)
    assert saved.digest == selected_again.digest == reloaded.digest
    moved = fingerprint_observation(observation(8.0, 20, 32))
    assert moved.digest != saved.digest
    text = describe_fingerprint_diff(saved, moved)
    assert "unit 7" in text
    assert "pos" in text
    assert "health" in text
    assert "orders" in text


def test_fog_snapshot_tags_do_not_change_the_fingerprint():
    from s2clientprotocol import raw_pb2, sc2api_pb2

    from sc2bench_env.backends.sharpy.engine_fingerprint import (
        describe_fingerprint_diff,
        fingerprint_observation,
    )

    def observation(tag: int, x: float):
        message = sc2api_pb2.Observation()
        mineral = message.raw_data.units.add()
        mineral.tag = tag
        mineral.unit_type = 665
        mineral.alliance = raw_pb2.Neutral
        mineral.display_type = raw_pb2.Snapshot
        mineral.pos.x = x
        mineral.pos.y = 50.5
        mineral.pos.z = 10.0
        own = message.raw_data.units.add()
        own.tag = 7
        own.unit_type = 45
        own.alliance = raw_pb2.Self
        own.display_type = raw_pb2.Visible
        own.pos.x = 3.0
        own.pos.y = 4.0
        return message

    first = fingerprint_observation(observation(100, 12.0))
    retagged = fingerprint_observation(observation(900, 12.0))
    assert first.digest == retagged.digest
    assert 100 not in first.units
    moved = fingerprint_observation(observation(100, 18.0))
    assert moved.digest != first.digest
    assert "snapshots" in describe_fingerprint_diff(first, moved)
    backend = SharpyBackend()
    backend._config = EpisodeConfig()
    assert backend.supports_savepoints() is True
    with pytest.raises(SavepointError, match="live engine slot"):
        backend.restore_engine_bookmark("missing")
    bridge = backend._bridge
    bridge.peak_ready["task"] = 3
    bridge.last_reported_completed["task"] = 2
    bridge.active_task_ids = ["task"]
    bridge.seen_entity_tags.add(("train", "marine", 7))
    bridge.entity_tracking_started = True
    bridge.structure_registry.id_for_tag(42)
    saved = backend.export_runtime_state()
    bridge.peak_ready.clear()
    bridge.seen_entity_tags.clear()
    bridge.entity_tracking_started = False
    bridge.structure_registry.reset()
    bridge.discard_macro_tasks = False
    backend.import_runtime_state(saved)
    assert bridge.peak_ready["task"] == 3
    assert bridge.last_reported_completed["task"] == 2
    assert ("train", "marine", 7) in bridge.seen_entity_tags
    assert bridge.entity_tracking_started is True
    assert bridge.structure_registry.tag_for_id("cc_0") == 42
    assert bridge.discard_macro_tasks is True
