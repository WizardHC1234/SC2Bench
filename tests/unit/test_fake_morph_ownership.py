"""Fake mode must preserve the same outbound source ownership as Sharpy."""
import pytest
from sc2bench_env import Environment, EpisodeConfig, ToolCall


@pytest.mark.parametrize("race,stock,target,tech", [
    ("protoss", {"dark_templar": 2}, "archon", {}),
    ("zerg", {"roach": 1}, "ravager", {"roach_warren": 1}),
])
def test_morph_waits_for_outbound_sources_and_resumes_after_return(race, stock, target, tech):
    env = Environment("fake", record_trajectory=False)

    def step(calls=(), seconds=1):
        turn = env.begin_tool_turn()
        results = [turn.call(ToolCall(name, args)) for name, args in calls]
        turn.call(ToolCall("advance", {"seconds": seconds}))
        return env.step(turn.finish())[0], results

    try:
        env.reset(EpisodeConfig(race=race))
        env.backend.units.update(stock)
        env.backend.buildings.update(tech)
        env.backend.supply_cap = 100
        env.backend.minerals = env.backend.vespene = 1000
        obs, _ = step([("dispatch_group", {"source": "group_0", "order": "attack",
                                           "target": "zone_15", "units": stock})])
        group = next(name for name in obs.combat if name != "group_0")
        obs, results = step([("train", {"target": target, "count": 1})], seconds=20)
        assert results[0].status == "staged"
        assert obs.units.get(target, 0) == 0
        assert all(obs.units.get(name, 0) == count for name, count in stock.items())
        assert obs.production_priority[0]["waiting_for"] == "source_unit_reserved"
        assert obs.combat[group]["alive"] == stock
        obs, _ = step([("retreat_group", {"group": group})], seconds=30)
        assert obs.units.get(target, 0) == 1
    finally:
        env.close()
