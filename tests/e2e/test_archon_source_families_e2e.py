"""Both native Templar combinations go through the public merge contract."""
import json
import os
from pathlib import Path

import pytest

from sc2bench_env import Environment, EpisodeConfig, ToolCall
from tests.e2e.test_base_abilities_e2e import _install

pytestmark = pytest.mark.skipif(os.environ.get("SC2BENCH_E2E") != "1", reason="Requires SC2")


@pytest.mark.parametrize("case,kinds", [
    ("dark", ["DARKTEMPLAR", "DARKTEMPLAR"]),
    ("mixed", ["HIGHTEMPLAR", "DARKTEMPLAR"]),
])
def test_native_archon_from_alternative_sources(monkeypatch, case, kinds):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    state = {"setup": False, "errors": [], "samples": []}

    async def collect(act):
        ai = act.ai
        if not state["setup"]:
            point = ai.bench_home_gather.home_point()
            await ai.client.debug_create_unit([[getattr(U, name), 1, point, ai.player_id] for name in kinds])
            state["setup"] = True
        state["errors"].extend({"seconds": ai.time, "ability": e.ability_id, "result": e.result}
                               for e in ai.state.action_errors)

    _install(monkeypatch, collect)
    env = Environment("sharpy", record_dir=f"records/platform_audit_contract_20261102/archon_{case}")

    def advance(seconds=2):
        turn = env.begin_tool_turn()
        turn.call(ToolCall("advance", {"seconds": seconds}))
        obs, feedback, done, info = env.step(turn.finish())
        assert not done, info
        state["samples"].append({"seconds": obs.game_time_seconds, "units": dict(obs.units),
                                 "events": feedback.events})
        return obs

    try:
        env.reset(EpisodeConfig(race="protoss", enemy_race="terran", opponent="veryeasy",
                                map_name="AcropolisLE", game_time_limit_seconds=90))
        obs = advance(4)
        assert "archon" in obs.available_targets["train"]
        turn = env.begin_tool_turn()
        state["staged"] = turn.call(ToolCall("train", {"target": "archon", "count": 1})).to_dict()
        assert state["staged"]["status"] == "staged"
        state["dispatch"] = turn.call(ToolCall("dispatch_group", {
            "source": "group_0", "order": "attack", "target": "zone_1",
            "units": {"dark_templar": 1}})).to_dict()
        assert state["dispatch"]["status"] == "rejected"
        turn.call(ToolCall("advance", {"seconds": 2}))
        env.step(turn.finish())
        for _ in range(12):
            obs = advance()
            if obs.units.get("archon", 0) == 1:
                break
        assert obs.units.get("archon", 0) == 1
        assert obs.units.get("high_templar", 0) == obs.units.get("dark_templar", 0) == 0
        assert not state["errors"], state["errors"]
    finally:
        state["record_directory"] = str(env.record_path)
        Path(f"analysis/platform_audit/archon_{case}_native.json").write_text(json.dumps(state, indent=2), encoding="utf8")
        env.close()
