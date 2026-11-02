"""Unpaid native morph reservations can be cancelled and dispatched immediately."""
import json
import os
from pathlib import Path
import pytest

from sc2bench_env import Environment, EpisodeConfig, ToolCall
from tests.e2e.test_base_abilities_e2e import _install

pytestmark = pytest.mark.skipif(os.environ.get("SC2BENCH_E2E") != "1", reason="Requires SC2")


@pytest.mark.parametrize("race,source,target", [
    ("protoss", "HIGHTEMPLAR", "archon"), ("zerg", "ROACH", "ravager"),
])
def test_cancel_waiting_native_morph_then_dispatch_without_extra_turn(monkeypatch, race, source, target):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    state = {"setup": False, "errors": [], "distance": 0}

    async def collect(act):
        ai = act.ai
        if not state["setup"]:
            point = ai.bench_home_gather.home_point()
            rows = [[getattr(U, source), 1, point, ai.player_id]]
            if race == "zerg":
                rows.extend([[U.SPAWNINGPOOL, 1, ai.start_location.offset((5, 0)), ai.player_id],
                             [U.ROACHWARREN, 1, ai.start_location.offset((-5, 0)), ai.player_id]])
            await ai.client.debug_create_unit(rows)
            state["setup"] = True
        for e in ai.state.action_errors:
            state["errors"].append({"seconds": ai.time, "ability": e.ability_id, "result": e.result})
        units = ai.units(getattr(U, source))
        if units:
            if "origin" not in state:
                state["origin"] = units.first.position
            if state.get("dispatched"):
                state["distance"] = max(state["distance"], units.first.distance_to(state["origin"]))

    _install(monkeypatch, collect)
    env = Environment("sharpy", record_dir=f"records/platform_audit_contract_20261102/cancel_morph_{race}")
    def advance(seconds):
        turn = env.begin_tool_turn()
        turn.call(ToolCall("advance", {"seconds": seconds}))
        return env.step(turn.finish())[0]

    try:
        env.reset(EpisodeConfig(race=race, enemy_race="terran", opponent="veryeasy",
                                map_name="AcropolisLE", game_time_limit_seconds=120))
        obs = advance(6)
        source_name = "high_templar" if race == "protoss" else "roach"
        turn = env.begin_tool_turn()
        assert turn.call(ToolCall("train", {"target": target, "count": 1})).status == "staged"
        turn.call(ToolCall("advance", {"seconds": 3}))
        obs, _, _, _ = env.step(turn.finish())
        assert obs.units.get(source_name) == 1 and not obs.units.get(target)
        state["unavailable_before_cancel"] = dict(env.backend.snapshot().info.get("unavailable_army") or {})
        state["releasable"] = dict(env.backend.snapshot().info.get("releasable_morph_sources") or {})
        turn = env.begin_tool_turn()
        state["cancel"] = turn.call(ToolCall("cancel", {"target_action": "train", "target": target})).to_dict()
        assert state["cancel"]["status"] == "staged"
        state["dispatch"] = turn.call(ToolCall("dispatch_group", {
            "source": "group_0", "order": "attack", "target": "zone_1", "units": {source_name: 1}})).to_dict()
        assert state["dispatch"]["status"] == "staged", state
        state["dispatched"] = True
        turn.call(ToolCall("advance", {"seconds": 3}))
        obs, feedback, _, _ = env.step(turn.finish())
        assert all(r.result == "accepted" for r in feedback.receipts)
        for _ in range(6):
            if state["distance"] > 2:
                break
            obs = advance(2)
        assert state["distance"] > 2, state
        assert not obs.production_priority and not obs.units.get(target)
        assert not state["errors"], state
    finally:
        state["record_directory"] = str(env.record_path)
        state.pop("origin", None)
        Path(f"analysis/platform_audit/cancel_morph_{race}_native.json").write_text(json.dumps(state, indent=2), encoding="utf8")
        env.close()


def test_started_archon_merge_is_not_cancelled_or_released(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    state = {"setup": False}
    async def collect(act):
        if not state["setup"]:
            await act.ai.client.debug_create_unit([[U.HIGHTEMPLAR, 2,
                act.ai.bench_home_gather.home_point(), act.ai.player_id]])
            state["setup"] = True
    _install(monkeypatch, collect)
    env = Environment("sharpy", record_dir="records/platform_audit_contract_20261102/cancel_started_archon")
    try:
        env.reset(EpisodeConfig(race="protoss", opponent="veryeasy", map_name="AcropolisLE",
                                game_time_limit_seconds=90))
        turn = env.begin_tool_turn()
        turn.call(ToolCall("advance", {"seconds": 6}))
        env.step(turn.finish())
        turn = env.begin_tool_turn()
        turn.call(ToolCall("train", {"target": "archon", "count": 1}))
        turn.call(ToolCall("advance", {"seconds": 1}))
        obs, _, _, _ = env.step(turn.finish())
        state["before_cancel"] = obs.to_dict()
        turn = env.begin_tool_turn()
        result = turn.call(ToolCall("cancel", {"target_action": "train", "target": "archon"}))
        state["cancel"] = result.to_dict()
        assert result.status == "no_op", result
        assert result.data["effect"]["removed_unstarted"] == 0
        dispatch = turn.call(ToolCall("dispatch_group", {"source": "group_0", "order": "attack",
                            "target": "zone_1", "units": {"high_templar": 2}}))
        assert dispatch.status == "rejected"
        turn.call(ToolCall("advance", {"seconds": 15}))
        obs, _, _, _ = env.step(turn.finish())
        assert obs.units.get("archon", 0) == 1
    finally:
        state["record_directory"] = str(env.record_path)
        Path("analysis/platform_audit/cancel_started_archon_native.json").write_text(json.dumps(state, indent=2), encoding="utf8")
        env.close()
