"""Cancelling unpaid morphs releases their sources in the same transaction."""
import pytest

from sc2bench_env import Environment, EpisodeConfig, ToolCall


@pytest.mark.parametrize("race,stock,target", [
    ("protoss", {"high_templar": 1}, "archon"),
    ("protoss", {"high_templar": 1, "dark_templar": 1}, "archon"),
    ("zerg", {"roach": 1}, "ravager"),
])
def test_same_turn_cancel_releases_morph_sources_for_dispatch(race, stock, target):
    env = Environment("fake", record_trajectory=False)
    try:
        env.reset(EpisodeConfig(race=race))
        env.backend.units.update(stock)
        turn = env.begin_tool_turn()
        assert turn.call(ToolCall("train", {"target": target, "count": 1})).status == "staged"
        assert turn.call(ToolCall("cancel", {"target_action": "train", "target": target})).status == "staged"
        result = turn.call(ToolCall("dispatch_group", {
            "source": "group_0", "order": "attack", "target": "zone_1", "units": stock}))
        assert result.status == "staged", result
        turn.call(ToolCall("advance", {"seconds": 1}))
        obs, feedback, _, _ = env.step(turn.finish())
        assert all(r.result == "accepted" for r in feedback.receipts)
        assert obs.own_forces.assigned == stock
        assert not obs.production_priority
    finally:
        env.close()


@pytest.mark.parametrize("race,stock,target", [
    ("protoss", {"high_templar": 1}, "archon"),
    ("zerg", {"roach": 1}, "ravager"),
])
def test_cancel_existing_waiting_morph_releases_sources_without_an_extra_advance(race, stock, target):
    env = Environment("fake", record_trajectory=False)
    try:
        env.reset(EpisodeConfig(race=race))
        env.backend.units.update(stock)
        env.backend.supply_cap = 100
        env.backend.buildings["roach_warren"] = 1
        env.backend.vespene = 0
        turn = env.begin_tool_turn()
        turn.call(ToolCall("train", {"target": target, "count": 1}))
        turn.call(ToolCall("advance", {"seconds": 1}))
        env.step(turn.finish())
        turn = env.begin_tool_turn()
        assert turn.call(ToolCall("cancel", {"target_action": "train", "target": target})).status == "staged"
        result = turn.call(ToolCall("dispatch_group", {
            "source": "group_0", "order": "attack", "target": "zone_1", "units": stock}))
        assert result.status == "staged", result
    finally:
        env.close()


def test_cancel_does_not_release_units_already_dispatched_in_the_transaction():
    env = Environment("fake", record_trajectory=False)
    try:
        env.reset(EpisodeConfig(race="protoss"))
        env.backend.units["high_templar"] = 3
        turn = env.begin_tool_turn()
        turn.call(ToolCall("train", {"target": "archon", "count": 1}))
        def dispatch(count, target="zone_1"):
            return turn.call(ToolCall("dispatch_group", {
                "source": "group_0", "order": "attack", "target": target,
                "units": {"high_templar": count}}))
        assert dispatch(1).status == "staged"
        turn.call(ToolCall("cancel", {"target_action": "train", "target": "archon"}))
        assert dispatch(2).status == "staged"
        assert dispatch(1, "zone_2").status == "rejected"
        turn.call(ToolCall("advance", {"seconds": 1}))
        obs, _, _, _ = env.step(turn.finish())
        assert obs.own_forces.assigned == {"high_templar": 3}
    finally:
        env.close()
