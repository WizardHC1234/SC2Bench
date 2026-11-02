"""Archon requests share both Templar types with dispatch and observation."""
import pytest

from sc2bench_env import Environment, EpisodeConfig, ToolCall
from sc2bench_env.backends.base import BackendSnapshot
from sc2bench_env.interface.actions import parse_decision
from sc2bench_env.runtime.observation_builder import idle_army_counts
from sc2bench_env.runtime.task_manager import TaskManager


@pytest.mark.parametrize("stock", [
    {"dark_templar": 2}, {"high_templar": 1, "dark_templar": 1},
    {"high_templar": 2},
])
def test_merge_reserves_both_source_types_before_same_turn_dispatch(stock):
    manager = TaskManager(race="protoss")
    batch = parse_decision([
        {"name": "train", "arguments": {"target": "archon", "count": 1}},
        {"name": "dispatch_group", "arguments": {
            "source": "group_0", "order": "attack", "target": "zone_1", "units": stock}},
        {"name": "advance", "arguments": {"seconds": 1}},
    ], race="protoss")
    receipts = manager.submit_decision(batch, game_time=0, idle_army=dict(stock))
    assert receipts[0].result == "accepted"
    assert receipts[1].result == "rejected"
    free = idle_army_counts(BackendSnapshot(units=stock), manager)
    assert all(free.get(name, 0) == 0 for name in stock)


def test_two_merge_requests_reserve_sources_once_and_cancel_releases_them():
    manager = TaskManager(race="protoss")
    manager.submit_decision(parse_decision([
        {"name": "train", "arguments": {"target": "archon", "count": 1}},
        {"name": "train", "arguments": {"target": "archon", "count": 1}},
        {"name": "advance", "arguments": {"seconds": 1}},
    ], race="protoss"), game_time=0)
    snapshot = BackendSnapshot(units={"high_templar": 1, "dark_templar": 4})
    assert idle_army_counts(snapshot, manager).get("dark_templar") == 1
    manager.submit_decision(parse_decision([
        {"name": "cancel", "arguments": {"target_action": "train", "target": "archon"}},
        {"name": "advance", "arguments": {"seconds": 1}},
    ], race="protoss"), game_time=1)
    assert idle_army_counts(snapshot, manager) == snapshot.units


@pytest.mark.parametrize("stock", [{"dark_templar": 2}, {"high_templar": 1, "dark_templar": 1}])
def test_fake_contract_consumes_the_same_legal_source_combinations(stock):
    env = Environment("fake", record_trajectory=False)
    try:
        env.reset(EpisodeConfig(race="protoss"))
        env.backend.units.update(stock)
        turn = env.begin_tool_turn()
        assert turn.call(ToolCall("train", {"target": "archon", "count": 1})).status == "staged"
        turn.call(ToolCall("advance", {"seconds": 20}))
        obs, _, _, _ = env.step(turn.finish())
        assert obs.units.get("archon", 0) == 1
        assert all(obs.units.get(name, 0) == 0 for name in stock)
    finally:
        env.close()


def test_dark_templar_alone_exposes_archon_and_knowledge_lists_both_sources():
    from sc2bench_env.runtime.observation_builder import available_targets
    from sc2bench_env.runtime.query_tools import query_unit_data
    assert "archon" in available_targets(race="protoss", units={"dark_templar": 2})["train"]
    row = query_unit_data(["archon"], race="protoss")["results"][0]
    assert row["source_options"] == ["high_templar", "dark_templar"]
    assert row["source_count"] == 2
    assert "high_templar" not in row["prerequisites"]
