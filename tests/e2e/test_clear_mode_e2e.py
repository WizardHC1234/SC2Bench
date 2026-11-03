"""Real engine: clearing destroys a seen structure, resumes search and accepts new orders.

Debug creates test preconditions only; movement, vision, damage and group
transitions use normal platform execution. Enable with SC2BENCH_E2E=1.
"""
import os

import pytest

from sc2bench_env import Environment, ToolCall
from sc2bench_env.interface.config import EpisodeConfig
from tests.e2e.test_base_abilities_e2e import _install

pytestmark = pytest.mark.skipif(os.environ.get("SC2BENCH_E2E") != "1", reason="Requires SC2")


@pytest.mark.parametrize("race,kind,map_name", [
    ("terran", "MARINE", "KairosJunctionLE"),
    ("protoss", "STALKER", "AcropolisLE"),
    ("zerg", "ROACH", "TritonLE"),
])
def test_native_clear_destroy_resume_switch_and_return(monkeypatch, tmp_path, race, kind, map_name):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2bench_env.backends.sharpy.acts import ActCombatMission

    state = {"seeded": False, "seen": False, "destroyed": False, "goals": set(), "instances": set()}

    async def collect(watch):
        ai = watch.ai
        if not state["seeded"]:
            origin = watch.zone_manager.own_natural.center_location.towards(ai.game_info.map_center, 12)
            point = await ai.find_placement(U.SUPPLYDEPOT, origin.towards(ai.game_info.map_center, 8))
            assert point is not None
            state.update(seeded=True, structure_point=point)
            await ai.client.debug_create_unit([
                [getattr(U, kind), 12, origin, ai.player_id],
                [U.SUPPLYDEPOT, 1, point, 2 if ai.player_id == 1 else 1],
            ])
        elif not state["destroyed"]:
            structures = [unit for unit in ai.enemy_structures if unit.distance_to(state["structure_point"]) < 2
                          and unit.is_visible and not unit.is_snapshot]
            if structures:
                state["seen"] = True
            elif state["seen"] and ai.is_visible(state["structure_point"]):
                state["destroyed"] = True

    original = ActCombatMission.execute

    async def observed(act):
        result = await original(act)
        if act.style == "clear" and act._bound:
            state["instances"].add(id(act))
            state["act"] = act
            if act.clear_search.current:
                state["goals"].add(act.clear_search.current.key)
        return result

    _install(monkeypatch, collect)
    monkeypatch.setattr(ActCombatMission, "execute", observed)
    env = Environment("sharpy", record_dir=tmp_path)

    def step(name=None, arguments=None, seconds=3):
        turn = env.begin_tool_turn()
        result = turn.call(ToolCall(name, arguments)) if name else None
        assert turn.call(ToolCall("advance", {"seconds": seconds})).status == "decision_ready"
        obs, feedback, done, info = env.step(turn.finish())
        assert not done, info
        return obs, result

    try:
        env.reset(EpisodeConfig(race=race, enemy_race="terran", map_name=map_name,
                                opponent="veryeasy", seed=20261103, game_time_limit_seconds=240))
        obs, _ = step(seconds=1)
        unit_name = kind.lower()
        assert obs.own_forces.free.get(unit_name, 0) >= 4
        obs, result = step("dispatch_group", {"source": "group_0", "units": {unit_name: 4}, "order": "attack", "mode": "clear"})
        assert result.status == "staged", result
        for _ in range(35):
            row = obs.combat["group_1"]
            assert row["style"] == "clear" and "search" in row, row
            if state["destroyed"] and len(state["goals"]) >= 2:
                break
            obs, _ = step()
        assert state["destroyed"] and len(state["goals"]) >= 2, state
        assert len(state["instances"]) == 1
        assert obs.combat["group_1"]["alive"].get(unit_name, 0) == 4
        assert obs.own_forces.army.get(unit_name, 0) == 12
        assert obs.own_forces.assigned.get(unit_name, 0) + obs.own_forces.free.get(unit_name, 0) == 12
        act, progress = state["act"], state["act"].clear_search
        turn = env.begin_tool_turn()
        result = turn.call(ToolCall("attack_group", {"group": "group_1", "mode": "clear"}))
        assert result.status == "no_op" and act.clear_search is progress
        turn.call(ToolCall("advance", {"seconds": 1}))
        obs, _, done, _ = env.step(turn.finish())
        assert not done
        home = next(row["zone_id"] for row in obs.zone_state if row["zone_role"] == "own_main")
        obs, result = step("defend_group", {"group": "group_1", "target": home})
        assert result.status == "staged" and obs.combat["group_1"]["style"] == "defend"
        assert "search" not in obs.combat["group_1"]
        obs, result = step("attack_group", {"group": "group_1", "mode": "clear"})
        assert result.status == "staged" and obs.combat["group_1"]["style"] == "clear"
        obs, result = step("retreat_group", {"group": "group_1"})
        assert result.status == "staged"
        for _ in range(25):
            if "group_1" not in obs.combat:
                break
            obs, _ = step()
        assert "group_1" not in obs.combat
        assert obs.combat["group_0"]["alive"].get(unit_name, 0) >= 4
        assert obs.own_forces.army.get(unit_name, 0) == 12
    finally:
        env.close()


def test_native_clear_finds_unseen_last_structure_and_finishes_game(monkeypatch, tmp_path):
    """No target hint: normal fog and search must expose the last enemy building."""
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    import sc2
    from sc2.bot_ai import BotAI
    from sc2.data import Race
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sc2.player import Bot
    from sc2bench_env.backends.sharpy.acts import ActCombatMission

    state = {"prepared": False, "seen_by_clear_group": False, "initial_enemy_sightings": None}

    class PassiveOpponent(BotAI):
        def __init__(self):
            super().__init__()
            self.distance_calculation_method = 0

        async def on_start(self):
            our_start = self.enemy_start_locations[0]
            candidates = [point for point in self.expansion_locations_list if point.distance_to(our_start) > 23]
            expansion = min(candidates, key=lambda point: point.distance_to(our_start))
            hidden = await self.find_placement(U.SUPPLYDEPOT, expansion.towards(self.game_info.map_center, 8))
            assert hidden is not None
            state["hidden"] = hidden  # fixture evidence only, never given to the controller
            await self.client.debug_create_unit([
                [U.SUPPLYDEPOT, 1, hidden, self.player_id],
                [U.MARINE, 12, our_start.towards(self.game_info.map_center, 5), 1],
            ])
            await self.client.debug_kill_unit(self.workers)

        async def on_step(self, iteration):
            depots = list(self.structures(U.SUPPLYDEPOT))
            if depots and not state["prepared"]:
                state["hidden_tag"] = depots[0].tag
                await self.client.debug_kill_unit([unit.tag for unit in self.structures if unit.type_id != U.SUPPLYDEPOT])
                state["prepared"] = True

    original_run = sc2.run_game
    original_execute = ActCombatMission.execute

    def controlled(game_map, players, **kwargs):
        result = original_run(game_map, [players[0], Bot(Race.Terran, PassiveOpponent())], **kwargs)
        return result[0] if isinstance(result, list) else result

    async def observe(act):
        result = await original_execute(act)
        if act.style == "clear" and any(unit.tag == state.get("hidden_tag") and unit.is_visible
                                         and not unit.is_snapshot for unit in act.ai.enemy_structures):
            state["seen_by_clear_group"] = True
        return result

    monkeypatch.setattr(sc2, "run_game", controlled)
    monkeypatch.setattr(ActCombatMission, "execute", observe)
    env = Environment("sharpy", record_dir=tmp_path)

    def step(name=None, arguments=None, seconds=5):
        turn = env.begin_tool_turn()
        result = turn.call(ToolCall(name, arguments)) if name else None
        assert turn.call(ToolCall("advance", {"seconds": seconds})).status == "decision_ready"
        return (*env.step(turn.finish()), result)

    try:
        env.reset(EpisodeConfig(race="terran", enemy_race="terran", opponent="veryeasy",
                                seed=20261104, game_time_limit_seconds=420))
        obs, _, done, _, _ = step(seconds=1)
        assert not done and state["prepared"]
        for _ in range(10):
            if obs.own_forces.free.get("marine", 0) >= 8:
                break
            obs, _, done, _, _ = step(seconds=2)
            assert not done
        state["initial_enemy_sightings"] = [row for row in obs.zone_state
                                            if any(row["visible_enemy_contents"].values())
                                            or any(row["last_seen_enemy_contents"].values())]
        assert not state["initial_enemy_sightings"]
        assert obs.own_forces.free.get("marine", 0) >= 8
        obs, _, done, _, result = step("dispatch_group", {"source": "group_0", "units": {"marine": 8},
                                                          "order": "attack", "mode": "clear"})
        assert result.status == "staged", result
        for _ in range(82):
            if done:
                break
            obs, _, done, _, _ = step()
        assert state["seen_by_clear_group"], state
        assert done and str(obs.game.result).split(".")[-1] == "Victory", (obs.game, state)
    finally:
        env.close()
