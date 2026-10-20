"""Regression checks for the platform defects found on 2026-10-20."""
from __future__ import annotations

import asyncio

from sc2bench_env import Environment, EpisodeConfig, ToolCall
from sc2bench_env.interface.actions import parse_advance_action
from sc2bench_env.runtime.query_tools import query_building_data, query_race_data, query_research_data
from sc2bench_env.versus.fake_game import FakeVersusGame


def _env(race: str) -> Environment:
    env = Environment("fake", record_trajectory=False)
    env.reset(EpisodeConfig(race=race, opponent="easy", game_time_limit_seconds=600))
    return env


def _play(env: Environment, calls, retry=None):
    turn = env.begin_tool_turn()
    staged = [(name, turn.call(ToolCall(name=name, arguments=args))) for name, args in calls]
    observation, feedback, done, info = env.step(turn.finish(), retry_ids=retry)
    return staged, feedback


def test_opening_does_not_train_a_worker():
    from sc2bench_env.backends.sharpy.bot import BenchBot

    bot = BenchBot(bridge=object(), adapter=object())
    assert bot.realtime_worker is False
    orders = []

    class _TownHall:
        def train(self, unit_type):
            orders.append(unit_type)

    bot.townhalls = [_TownHall()]
    asyncio.run(bot.start_first_worker())
    assert orders == []


def test_reinforce_retry_is_idempotent():
    env = _env("terran")
    env.backend.units["marine"] = 10
    _play(env, [
        ("dispatch_group", {"source": "group_0", "units": {"marine": 4}, "order": "defend", "target": "zone_1"}),
        ("advance", {"seconds": 1}),
    ])
    group = next(demand.group for demand in env.task_manager.active_demands() if demand.group)
    _play(env, [
        ("reinforce_group", {"source": "group_0", "destination": group, "units": {"marine": 2}}),
        ("advance", {"seconds": 1}),
    ], retry=["retry-reinforce"])
    turn = env.begin_tool_turn()
    again = turn.call(ToolCall(
        name="reinforce_group",
        arguments={"source": "group_0", "destination": group, "units": {"marine": 2}},
    ))
    turn.call(ToolCall(name="advance", arguments={"seconds": 1}))
    _, feedback, _, _ = env.step(turn.finish(), retry_ids=["retry-reinforce"])
    assert again.status == "staged"
    assert feedback.receipts[0].result == "ignored_duplicate_action_id"
    owned = next(demand.units["marine"] for demand in env.task_manager.active_demands() if demand.group == group)
    assert owned == 6
    env.close()


def test_same_turn_dispatch_cannot_overdraft_army():
    env = _env("terran")
    env.backend.units["marine"] = 10
    turn = env.begin_tool_turn()
    first = turn.call(ToolCall(name="dispatch_group", arguments={
        "source": "group_0", "units": {"marine": 6}, "order": "attack", "target": "zone_2",
    }))
    second = turn.call(ToolCall(name="dispatch_group", arguments={
        "source": "group_0", "units": {"marine": 6}, "order": "attack", "target": "zone_3",
    }))
    turn.call(ToolCall(name="advance", arguments={"seconds": 1}))
    _, feedback, _, _ = env.step(turn.finish(), retry_ids=["id-a"])
    assert first.status == "staged"
    assert second.status == "rejected"
    assert [receipt.result for receipt in feedback.receipts] == ["accepted"]
    assert "id-b" not in env.task_manager.seen_action_ids
    env.backend.units["marine"] = 20
    _, feedback = _play(env, [
        ("dispatch_group", {"source": "group_0", "units": {"marine": 6}, "order": "attack", "target": "zone_4"}),
        ("advance", {"seconds": 1}),
    ], retry=["id-b"])
    assert feedback.receipts[0].result == "accepted"
    env.close()


def test_same_turn_dispatch_keeps_legal_reservations():
    env = _env("terran")
    env.backend.units["marine"] = 10
    turn = env.begin_tool_turn()
    first = turn.call(ToolCall(name="dispatch_group", arguments={
        "source": "group_0", "units": {"marine": 6}, "order": "attack", "target": "zone_2",
    }))
    second = turn.call(ToolCall(name="dispatch_group", arguments={
        "source": "group_0", "units": {"marine": 4}, "order": "attack", "target": "zone_3",
    }))
    turn.call(ToolCall(name="advance", arguments={"seconds": 1}))
    _, feedback, _, _ = env.step(turn.finish())
    assert first.status == "staged"
    assert second.status == "staged"
    assert [receipt.result for receipt in feedback.receipts] == ["accepted", "accepted"]
    env.close()


def test_partial_return_home_keeps_retry_id():
    env = _env("terran")
    env.backend.units["marine"] = 6
    _play(env, [
        ("dispatch_group", {"source": "group_0", "units": {"marine": 4}, "order": "defend", "target": "zone_1"}),
        ("advance", {"seconds": 1}),
    ])
    group = next(demand.group for demand in env.task_manager.active_demands() if demand.group)
    _, feedback = _play(env, [
        ("reinforce_group", {"source": group, "destination": "group_0", "units": {"marine": 2}}),
        ("advance", {"seconds": 1}),
    ], retry=["home-1"])
    assert feedback.receipts[0].action_id == "home-1"
    assert "home-1" in env.task_manager.seen_action_ids
    turn = env.begin_tool_turn()
    again = turn.call(ToolCall(
        name="reinforce_group",
        arguments={"source": group, "destination": "group_0", "units": {"marine": 2}},
    ))
    assert again.status == "no_op"
    withdrawing = [
        demand for demand in env.task_manager.demands.values() if demand.withdrawing
    ]
    assert len(withdrawing) == 1
    env.close()


def test_advance_rejects_non_finite_seconds():
    env = _env("terran")
    turn = env.begin_tool_turn()
    result = turn.call(ToolCall(name="advance", arguments={"seconds": float("nan")}))
    assert result.status == "rejected"
    try:
        parse_advance_action({"name": "advance", "arguments": {"seconds": float("inf")}})
        raised = False
    except Exception:
        raised = True
    assert raised
    env.close()


def test_zerg_fake_production_contract():
    env = _env("zerg")
    backend = env.backend
    backend.minerals = 2000
    backend.vespene = 2000
    backend.supply_cap = 200
    backend.buildings["spawning_pool"] = 1
    _, feedback = _play(env, [
        ("train", {"target": "zergling", "count": 1}),
        ("advance", {"seconds": 30}),
    ])
    assert feedback.receipts[0].result == "accepted"
    assert backend.units.get("zergling") == 2

    backend.buildings.update({"baneling_nest": 1})
    _play(env, [("train", {"target": "baneling", "count": 1}), ("advance", {"seconds": 20})])
    assert backend.units.get("zergling", 0) == 1
    assert backend.units.get("baneling") == 1

    before = backend.units.get("drone")
    _play(env, [("build", {"target": "evolution_chamber"}), ("advance", {"seconds": 40})])
    assert backend.buildings.get("evolution_chamber") == 1
    assert backend.units.get("drone") == before - 1

    backend.buildings.pop("hatchery", None)
    backend.buildings["lair"] = 1
    backend.structure_types["townhall_0"] = "lair"
    drones = backend.units.get("drone")
    _play(env, [("train", {"target": "drone", "count": 1}), ("advance", {"seconds": 20})])
    assert backend.units.get("drone") == drones + 1
    env.close()


def test_knowledge_does_not_advertise_unusable_targets():
    building = query_building_data(["nydus_network"], race="zerg")["results"][0]
    assert building["availability"] == "knowledge_only"
    from sc2bench_env.catalog.knowledge import _profile, _snapshot, game_data

    linux = _snapshot(str(_profile("linux_official.json")["snapshot_dir"]))
    with game_data(linux):
        research = query_research_data(["microbial_shroud"], race="zerg")["results"][0]
        listed = query_race_data("zerg")["research"]
    assert research["availability"] == "unavailable"
    assert "microbial_shroud" not in listed
    observable = query_race_data("zerg")["observable_only"]
    listed = set(observable["units"]) | set(observable["buildings"])
    assert {"broodling", "creep_tumor", "nydus_worm"} <= listed


def test_fake_versus_opening_follows_race():
    game = FakeVersusGame()
    left, right = game.start(EpisodeConfig(
        race="zerg", enemy_race="protoss", opponent="easy", game_time_limit_seconds=30,
    ))
    assert left.units["drone"] == 12
    assert left.buildings["hatchery"] == 1
    assert right.units["probe"] == 12
    assert right.buildings["nexus"] == 1
    game.close()


def test_warp_gate_morph_does_not_convert_a_second_gateway():
    from sc2.ids.unit_typeid import UnitTypeId

    from sc2bench_env.backends.sharpy.races.protoss_warp import ActWarpGateConversion

    class _Gate:
        def __init__(self, tag):
            self.tag = tag
            self.orders = []

        def __call__(self, ability):
            self.orders.append(ability)

    class _Units:
        def __init__(self, items):
            self._items = list(items)
            self.amount = len(self._items)

        @property
        def ready(self):
            return self._items

    class _AI:
        def __init__(self):
            self.gates = [_Gate(1), _Gate(2)]
            self.warpgates = []
            self.minerals = 500
            self.vespene = 500
            self.bench_warp_reserve_tags = set()
            self.unit_tags_received_action = set()

        def structures(self, type_id):
            if type_id == UnitTypeId.WARPGATE:
                return _Units(self.warpgates)
            return _Units(self.gates)

        def already_pending_upgrade(self, upgrade):
            return 1

    ai = _AI()
    act = ActWarpGateConversion(1)
    act.ai = ai
    asyncio.run(act.execute())
    asyncio.run(act.execute())
    ordered = [gate.tag for gate in ai.gates if gate.orders]
    assert ordered == [1]
