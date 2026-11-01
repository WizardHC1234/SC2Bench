"""Live checks for the checklist rows that were still unit or open.

Enable with SC2BENCH_E2E=1.
"""
from __future__ import annotations


from tests.helpers.drive import drive, play
import os

import pytest

from tests.e2e.test_base_abilities_e2e import _install, _wait
from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def _step(env, actions, seconds=1):
    obs, feedback, terminated, info = drive(env, [*actions, _wait(seconds)])
    if not terminated:
        assert_obs_consistent(obs)
    return obs, feedback, terminated, info


def _until(env, predicate, limit=16):
    obs = None
    for _ in range(limit):
        obs, _, terminated, _ = _step(env, [])
        assert not terminated
        if predicate(obs):
            return obs
    raise AssertionError({
        "building": getattr(obs, "building", None),
        "training": getattr(obs, "training", None),
        "research": getattr(obs, "research", None),
        "cap": getattr(getattr(obs, "resources", None), "supply_cap", None),
        "used": getattr(getattr(obs, "resources", None), "supply_used", None),
        "units": getattr(obs, "units", None),
        "scan": getattr(obs, "scan_ready", None),
        "upgrades": getattr(obs, "upgrades", None),
    })


def _rejected(feedback) -> bool:
    return any(event.get("type") == "decision_rejected" for event in feedback.events)


def _remaining(obs, target) -> int:
    return sum(
        int(row.get("remaining") or 0)
        for row in obs.production_priority
        if row.get("action") == "train" and row.get("target") == target
    )


def test_e2e_terran_decision_supply_scan_detection(monkeypatch) -> None:
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.position import Point2

    from agents.llm_agent import LLMAgent
    from sc2bench_env import Environment
    from sc2bench_env.benchmark.runner import AgentInput
    from sc2bench_env.interface.config import EpisodeConfig

    state = {}

    async def collect(act):
        if state.get("resources") and not state.get("filled"):
            await act.ai.client.debug_all_resources()
            state["filled"] = True
        if state.get("fast") and not state.get("sped"):
            await act.ai.client.debug_fast_build()
            state["sped"] = True
        if state.get("racks") and not state.get("racked"):
            start = act.ai.start_location
            rows = []
            for index in range(4):
                point = start.offset(Point2((6, 4 + index * 3)))
                rows.append([UnitTypeId.BARRACKS, 1, point, act.ai.player_id])
            await act.ai.client.debug_create_unit(rows)
            state["racked"] = True
        if state.get("fill_supply") and not state.get("supplied"):
            near = act.ai.start_location
            rows = []
            for _ in range(28):
                point = await act.ai.find_placement(UnitTypeId.SUPPLYDEPOT, near, placement_step=3)
                if point is None:
                    break
                rows.append([UnitTypeId.SUPPLYDEPOT, 1, point, act.ai.player_id])
                near = point
            if rows:
                await act.ai.client.debug_create_unit(rows)
            state["supplied"] = True
            state["placed"] = len(rows)
        if state.get("energy") and not state.get("energized"):
            tags = [unit.tag for unit in act.ai.structures(UnitTypeId.ORBITALCOMMAND)]
            if tags:
                await act.ai.client.debug_set_unit_value(tags, 1, 180)
                state["energized"] = True
        if state.get("detect") == "hide" and "hidden_seen" not in state:
            home = act.ai.start_location
            foe = 2 if act.ai.player_id == 1 else 1
            await act.ai.client.debug_create_unit([
                [UnitTypeId.MARINE, 6, home, act.ai.player_id],
                [UnitTypeId.DARKTEMPLAR, 1, home.towards(act.ai.game_info.map_center, 4), foe],
            ])
            state["hidden_seen"] = 0
        elif state.get("detect") == "hide":
            templar = [unit for unit in act.ai.all_enemy_units if unit.type_id == UnitTypeId.DARKTEMPLAR]
            tag = templar[0].tag if templar else None
            state["templar_tag"] = tag
            aimed = any(
                getattr(order, "target", None) == tag
                for marine in act.ai.units(UnitTypeId.MARINE)
                for order in marine.orders
            )
            visible = any(getattr(unit, "is_visible", False) and not getattr(unit, "is_cloaked", False) for unit in templar)
            state["hidden_seen"] = 1 if aimed or visible else 0
        elif state.get("detect") == "show" and "turret" not in state:
            await act.ai.client.debug_create_unit(
                [[UnitTypeId.MISSILETURRET, 1, act.ai.start_location, act.ai.player_id]]
            )
            state["turret"] = True
        elif state.get("detect") == "show":
            tag = state.get("templar_tag")
            templar = [unit for unit in act.ai.all_enemy_units if unit.type_id == UnitTypeId.DARKTEMPLAR]
            visible = any(getattr(unit, "is_visible", False) for unit in templar)
            aimed = False
            for marine in act.ai.units(UnitTypeId.MARINE):
                for order in marine.orders:
                    if getattr(order, "target", None) == tag:
                        aimed = True
            state["revealed"] = visible or aimed
        if state.get("kill_scout") and not state.get("scout_killed"):
            for worker in list(act.ai.workers):
                if not getattr(worker, "is_gathering", False) and not getattr(worker, "is_returning", False):
                    await act.ai.client.debug_kill_unit([worker.tag])
                    break
            state["scout_killed"] = True

    _install(monkeypatch, collect)
    replies = [
        {"tool_calls": [
            {"id": "q", "name": "query_map_overview", "arguments": {}},
        ], "content": "I need the map before training.", "raw_content": ""},
        {"tool_calls": [
            {"id": "t", "name": "train", "arguments": {"target": "marine", "count": 1}},
        ], "content": "Train one marine.", "raw_content": ""},
        {"tool_calls": [
            {"id": "v", "name": "advance", "arguments": {"seconds": 1}},
        ], "content": "Advance after the train order.", "raw_content": ""},
    ]
    seen = {"n": 0}

    def call_llm(messages, tools=None):
        _ = messages, tools
        reply = replies[seen["n"]]
        seen["n"] += 1
        return reply

    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(
            race="terran", enemy_race="zerg", opponent="veryeasy", game_time_limit_seconds=240,
        ))
        names = [spec.name for spec in env.tool_specs()]
        assert "scan" in names and "call_mule" in names and "chrono_boost" not in names
        agent = LLMAgent(call_llm, verbose=False, skill_name="none")
        tool_turn = env.begin_tool_turn()
        agent(AgentInput(
            obs, None, tuple(env.tool_specs()), tool_turn.call,
        ))
        assert tool_turn.finish().to_dicts()[-1]["name"] == "advance"

        obs, feedback, terminated, _ = _step(env, [
            {"name": "train", "arguments": {"target": "marine", "count": 1}},
            {"name": "frobnicate", "arguments": {}},
        ])
        assert not terminated and _rejected(feedback)
        assert _remaining(obs, "marine") == 0

        obs, _, terminated, _ = _step(env, [{"name": "train", "arguments": {"target": "marine", "count": 1}}])
        assert not terminated
        first = _remaining(obs, "marine")
        assert first >= 1
        obs, _, terminated, _ = _step(env, [{"name": "train", "arguments": {"target": "marine", "count": 1}}])
        assert _remaining(obs, "marine") > first
        obs, feedback, terminated, _ = _step(env, [
            {"name": "cancel", "arguments": {"target_action": "train", "target": "marine"}},
        ])
        assert not terminated
        assert _remaining(obs, "marine") == 0
        assert feedback.receipts[0].result == "accepted"

        state["resources"] = True
        state["fast"] = True
        main = next(row["zone_id"] for row in obs.zone_state if row.get("zone_role") == "own_main")
        before_gas = next(row for row in obs.map_control.base_resources if row["zone_id"] == main)
        obs, _, _, _ = _step(env, [
            {"name": "build", "arguments": {"target": "supply_depot"}},
            {"name": "build", "arguments": {"target": "barracks"}},
            {"name": "build", "arguments": {"target": "refinery"}},
            {"name": "build", "arguments": {"target": "engineering_bay"}},
        ])
        obs = _until(
            env,
            lambda item: all(
                int((item.building.get(name) or {}).get("completed") or 0) >= 1
                for name in ("barracks", "engineering_bay", "refinery")
            ),
        )
        after_gas = next(row for row in obs.map_control.base_resources if row["zone_id"] == main)
        assert int(after_gas["available_geyser_slots"]) < int(before_gas["available_geyser_slots"])

        free = max(0, int(obs.resources.supply_cap) - int(obs.resources.supply_used))
        obs, _, _, _ = _step(env, [{"name": "train", "arguments": {"target": "marine", "count": free}}])
        obs = _until(
            env,
            lambda item: int(item.resources.supply_used) >= int(item.resources.supply_cap) - 1
            and int(item.units.get("marine", 0) or 0) >= 1,
        )
        obs, _, _, _ = _step(env, [{"name": "train", "arguments": {"target": "marine", "count": 4}}])
        obs = _until(env, lambda item: item.training.get("marine", {}).get("waiting_for") == "supply")
        state["fill_supply"] = True
        obs = _until(env, lambda item: int(item.resources.supply_cap) >= 200, limit=8)
        assert int(obs.resources.supply_cap) == 200
        obs = _until(
            env,
            lambda item: int(item.units.get("marine", 0) or 0) >= 1
            or int(item.training.get("marine", {}).get("in_production") or 0) >= 1,
        )

        obs, _, _, _ = _step(env, [{"name": "research", "arguments": {"target": "infantry_weapons_1"}}])
        obs = _until(env, lambda item: item.research.get("infantry_weapons_1") == "completed", limit=20)
        assert "infantry_weapons_1" not in obs.available_targets.get("research", [])
        obs, _, _, _ = _step(env, [{"name": "build", "arguments": {"target": "factory"}}])
        obs = _until(env, lambda item: int((item.building.get("factory") or {}).get("completed") or 0) >= 1)
        obs, _, _, _ = _step(env, [{"name": "build", "arguments": {"target": "armory"}}])
        obs = _until(env, lambda item: int((item.building.get("armory") or {}).get("completed") or 0) >= 1)
        assert "infantry_weapons_2" in obs.available_targets.get("research", [])
        obs, feedback, _, _ = _step(env, [{"name": "research", "arguments": {"target": "infantry_weapons_1"}}])
        assert feedback.receipts[0].result == "idempotent_noop"

        state["detect"] = "hide"
        obs, _, _, _ = _step(env, [], 2)
        assert state.get("hidden_seen") == 0
        state["detect"] = "show"
        obs = _until(env, lambda item: state.get("revealed"), limit=8)

        cc = next(row["id"] for row in obs.structures if str(row.get("id", "")).startswith("townhall_"))
        obs, _, _, _ = _step(env, [{"name": "morph_townhall", "arguments": {"target": cc, "to": "orbital_command"}}])
        obs = _until(env, lambda item: int(item.orbital_count or 0) >= 1, limit=16)
        state["energy"] = True
        obs = _until(env, lambda item: int(item.scan_ready or 0) >= 1, limit=8)
        before_energy = max(obs.abilities.get("orbital_energies") or [0])
        zone = next(row["zone_id"] for row in obs.zone_state if row.get("zone_role") == "enemy_main")
        obs, _, _, _ = _step(env, [{"name": "scan", "arguments": {"target": zone}}])
        after_energy = max(obs.abilities.get("orbital_energies") or [0])
        assert after_energy < before_energy
        obs, _, _, _ = _step(env, [{"name": "call_mule", "arguments": {}}])
        obs = _until(env, lambda item: int(item.units.get("mule", 0) or 0) >= 1, limit=8)

        route = [row["zone_id"] for row in obs.zone_state if row.get("zone_role") == "enemy_main"]
        obs, _, _, _ = _step(env, [{"name": "scout", "arguments": {"route": route or [zone]}}])
        assert obs.scouting
        state["kill_scout"] = True
        obs = _until(env, lambda item: not item.scouting, limit=8)
    finally:
        env.close()


def test_e2e_protoss_power_and_warp(monkeypatch) -> None:
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.position import Point2

    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    state = {}

    async def collect(act):
        if state.get("resources") and not state.get("filled"):
            await act.ai.client.debug_all_resources()
            state["filled"] = True
        if state.get("fast") and not state.get("sped"):
            await act.ai.client.debug_fast_build()
            state["sped"] = True
        if state.get("kill_pylons") and not state.get("killed"):
            tags = [unit.tag for unit in act.ai.structures(UnitTypeId.PYLON)]
            if tags:
                await act.ai.client.debug_kill_unit(tags)
            state["killed"] = True
        if state.get("repower") and not state.get("repowered"):
            gates = list(act.ai.structures(UnitTypeId.WARPGATE)) or list(act.ai.structures(UnitTypeId.GATEWAY))
            if gates:
                await act.ai.client.debug_create_unit(
                    [[UnitTypeId.PYLON, 1, gates[0].position, act.ai.player_id]]
                )
                state["repowered"] = True
        state["warp_gates"] = act.ai.structures(UnitTypeId.WARPGATE).ready.amount

    _install(monkeypatch, collect)
    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(
            race="protoss", enemy_race="terran", opponent="veryeasy", game_time_limit_seconds=180,
        ))
        names = [spec.name for spec in env.tool_specs()]
        assert "chrono_boost" in names
        assert "scan" not in names and "call_mule" not in names
        obs, feedback, terminated, _ = _step(env, [{"name": "scan", "arguments": {"target": "zone_0"}}])
        assert not terminated and _rejected(feedback)
        obs, feedback, terminated, _ = _step(env, [{"name": "call_mule", "arguments": {}}])
        assert not terminated and _rejected(feedback)

        state["resources"] = True
        state["fast"] = True
        obs, _, _, _ = _step(env, [{"name": "build", "arguments": {"target": "pylon"}}])
        obs = _until(env, lambda item: int((item.building.get("pylon") or {}).get("completed") or 0) >= 1)
        obs, _, _, _ = _step(env, [{"name": "build", "arguments": {"target": "gateway"}}])
        obs = _until(env, lambda item: int((item.building.get("gateway") or {}).get("completed") or 0) >= 1)
        obs, _, _, _ = _step(env, [{"name": "build", "arguments": {"target": "cybernetics_core"}}])
        obs = _until(env, lambda item: int((item.building.get("cybernetics_core") or {}).get("completed") or 0) >= 1)
        obs, _, _, _ = _step(env, [{"name": "research", "arguments": {"target": "warp_gate"}}])
        obs = _until(env, lambda item: state.get("warp_gates", 0) >= 1, limit=20)
        assert "zealot" in obs.available_targets.get("train", [])

        state["kill_pylons"] = True
        obs, _, _, _ = _step(env, [{"name": "train", "arguments": {"target": "zealot", "count": 2}}], 1)
        zealots = int(obs.units.get("zealot", 0) or 0)
        produced = int(obs.training.get("zealot", {}).get("in_production") or 0)
        obs, _, _, _ = _step(env, [], 3)
        assert int(obs.units.get("zealot", 0) or 0) == zealots
        assert int(obs.training.get("zealot", {}).get("in_production") or 0) == produced
        state["repower"] = True
        obs = _until(
            env,
            lambda item: int(item.units.get("zealot", 0) or 0) > zealots
            or int(item.training.get("zealot", {}).get("in_production") or 0) > produced,
            limit=12,
        )
        _ = Point2
    finally:
        env.close()


def test_e2e_zerg_morph_and_capital(monkeypatch) -> None:
    from sc2.ids.unit_typeid import UnitTypeId

    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    state = {}

    async def collect(act):
        if state.get("resources") and not state.get("filled"):
            await act.ai.client.debug_all_resources()
            state["filled"] = True
        if state.get("fast") and not state.get("sped"):
            await act.ai.client.debug_fast_build()
            state["sped"] = True
        state["upgrade_all"] = False
        if state.get("spawn") and not state.get("spawned"):
            home = act.ai.start_location
            foe = 2 if act.ai.player_id == 1 else 1
            center = act.ai.game_info.map_center
            await act.ai.client.debug_create_unit([
                [UnitTypeId.ULTRALISKCAVERN, 1, home.towards(center, 8), act.ai.player_id],
                [UnitTypeId.ULTRALISK, 1, home, act.ai.player_id],
                [UnitTypeId.BROODLORD, 1, home.towards(center, 6), act.ai.player_id],
                [UnitTypeId.OVERSEER, 1, home.towards(center, 5), act.ai.player_id],
                [UnitTypeId.MARINE, 8, home.towards(center, 14), foe],
            ])
            state["spawned"] = True
        if state.get("trace") and "abilities" not in state:
            lings = list(act.ai.units(UnitTypeId.ZERGLING))
            if lings:
                available = await act.ai.get_available_abilities(
                    [lings[0]], ignore_resource_requirements=True,
                )
                state["abilities"] = [item.name for item in available[0]]
        if state.get("sample"):
            from sc2.ids.upgrade_id import UpgradeId
            ultras = list(act.ai.units(UnitTypeId.ULTRALISK))
            marines = [unit for unit in act.ai.all_enemy_units if unit.type_id == UnitTypeId.MARINE]
            if UpgradeId.ANABOLICSYNTHESIS in act.ai.state.upgrades:
                state["upgraded"] = True
            if ultras and state.get("speed_before") is None and not state.get("upgraded"):
                state["speed_before"] = float(getattr(ultras[0], "movement_speed", 0) or 0)
            overs = list(act.ai.units(UnitTypeId.OVERSEER))
            if overs and not state.get("overseer_energy"):
                await act.ai.client.debug_set_unit_value([overs[0].tag], 1, 200)
                state["overseer_energy"] = True
            if state.get("upgraded") and marines and not state.get("attacked"):
                if ultras:
                    ultras[0].attack(marines[0])
                lords = list(act.ai.units(UnitTypeId.BROODLORD))
                if lords:
                    lords[0].attack(marines[0])
                state["attacked"] = True
            if ultras and state.get("attacked") and "origin" not in state:
                state["origin"] = (float(ultras[0].position.x), float(ultras[0].position.y))
                state["origin_time"] = float(act.ai.time)
            if ultras and state.get("origin") and state.get("speed_after") is None:
                if float(act.ai.time) >= float(state["origin_time"]) + 1:
                    dx = float(ultras[0].position.x) - state["origin"][0]
                    dy = float(ultras[0].position.y) - state["origin"][1]
                    state["traveled"] = (dx * dx + dy * dy) ** 0.5
                    state["speed_after"] = state["traveled"]
                    state["buffs"] = [
                        int(getattr(buff, "value", buff))
                        for buff in (getattr(ultras[0]._proto, "buff_ids", None) or [])
                    ]
            names = {unit.type_id.name for unit in act.ai.units}
            found = set(state.get("seen") or [])
            found.update(names)
            state["seen"] = sorted(found)
            if not any("CHANGELING" in name for name in found):
                from sc2.ids.ability_id import AbilityId
                for overseer in list(act.ai.units(UnitTypeId.OVERSEER)) + list(
                    act.ai.units(UnitTypeId.OVERSEERSIEGEMODE)
                ):
                    if float(getattr(overseer, "energy", 0) or 0) >= 50:
                        overseer(AbilityId.SPAWNCHANGELING_SPAWNCHANGELING)
                        break

    _install(monkeypatch, collect)
    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(
            race="zerg", enemy_race="terran", opponent="veryeasy", game_time_limit_seconds=180,
        ))
        names = [spec.name for spec in env.tool_specs()]
        assert "inject_larva" in names and "spawn_creep_tumor" in names
        assert "scan" not in names and "call_mule" not in names
        obs, feedback, terminated, _ = _step(env, [{"name": "scan", "arguments": {"target": "zone_0"}}])
        assert not terminated and _rejected(feedback)

        state["resources"] = True
        state["fast"] = True
        obs, _, _, _ = _step(env, [
            {"name": "build", "arguments": {"target": "spawning_pool"}},
            {"name": "train", "arguments": {"target": "zergling", "count": 6}},
        ])
        obs = _until(env, lambda item: int(item.units.get("zergling", 0) or 0) >= 4, limit=16)
        obs, _, _, _ = _step(env, [{"name": "build", "arguments": {"target": "baneling_nest"}}])
        obs = _until(env, lambda item: int((item.building.get("baneling_nest") or {}).get("completed") or 0) >= 1, limit=16)
        before_larva = int(next(row["ready_grounded"] for row in obs.production if row["facility"] == "larva"))
        before_lings = int(obs.units.get("zergling", 0) or 0)
        state["trace"] = True
        obs, _, _, _ = _step(env, [{"name": "train", "arguments": {"target": "baneling", "count": 2}}])
        morphed = False
        for _ in range(16):
            if int(obs.units.get("baneling", 0) or 0) >= 1 or int(obs.units.get("zergling", 0) or 0) < before_lings:
                morphed = True
                break
            if state.get("abilities") is not None and "MORPHZERGLINGTOBANELING_BANELING" not in state["abilities"]:
                break
            obs, _, _, _ = _step(env, [])
        offered = "MORPHZERGLINGTOBANELING_BANELING" in (state.get("abilities") or [])
        if offered:
            assert morphed, state.get("abilities")
            after_larva = int(next(row["ready_grounded"] for row in obs.production if row["facility"] == "larva"))
            assert after_larva >= before_larva - 1
        else:
            state["baneling_client_unavailable"] = True

        cc = next(row["id"] for row in obs.structures if str(row.get("id", "")).startswith("townhall_"))
        obs, _, _, _ = _step(env, [{"name": "morph_townhall", "arguments": {"target": cc, "to": "lair"}}])
        obs = _until(env, lambda item: int((item.building.get("lair") or {}).get("completed") or 0) >= 1, limit=16)
        obs, _, _, _ = _step(env, [{"name": "build", "arguments": {"target": "spire"}}])
        obs = _until(env, lambda item: int((item.building.get("spire") or {}).get("completed") or 0) >= 1, limit=16)
        spires = int((obs.building.get("spire") or {}).get("completed") or 0)
        obs, _, _, _ = _step(env, [{"name": "build", "arguments": {"target": "infestation_pit"}}])
        obs = _until(env, lambda item: int((item.building.get("infestation_pit") or {}).get("completed") or 0) >= 1, limit=16)
        cc = next(row["id"] for row in obs.structures if str(row.get("id", "")).startswith("townhall_"))
        obs, _, _, _ = _step(env, [{"name": "morph_townhall", "arguments": {"target": cc, "to": "hive"}}])
        obs = _until(env, lambda item: int((item.building.get("hive") or {}).get("completed") or 0) >= 1, limit=16)
        obs, _, _, _ = _step(env, [{"name": "build", "arguments": {"target": "greater_spire"}}])
        obs = _until(env, lambda item: int((item.building.get("greater_spire") or {}).get("completed") or 0) >= 1, limit=16)
        assert int((obs.building.get("spire") or {}).get("completed") or 0) <= spires

        state["spawn"] = True
        state["sample"] = True
        state["speed_before"] = None
        state["speed_after"] = None
        obs, _, _, _ = _step(env, [], 2)
        assert state.get("speed_before", 0) > 0
        obs, _, _, _ = _step(env, [{"name": "research", "arguments": {"target": "anabolic_synthesis"}}])
        for _ in range(12):
            if "anabolic_synthesis" in (obs.upgrades or []):
                break
            obs, _, _, _ = _step(env, [])
        if "anabolic_synthesis" in (obs.upgrades or []):
            for _ in range(8):
                seen = set(state.get("seen") or [])
                if any("BROODLING" in name for name in seen) and any("CHANGELING" in name for name in seen):
                    break
                obs, _, _, _ = _step(env, [])
            seen = set(state.get("seen") or [])
            assert any("BROODLING" in name for name in seen), sorted(seen)
            assert any("CHANGELING" in name for name in seen), sorted(seen)
            assert "anabolic_synthesis" in (obs.upgrades or [])
        else:
            state["anabolic_research"] = obs.research.get("anabolic_synthesis")
            raise AssertionError(state.get("anabolic_research"))
    finally:
        env.close()


def test_e2e_versus_and_quit(monkeypatch) -> None:
    from sc2.ids.unit_typeid import UnitTypeId

    from sc2bench_env.interface.config import EpisodeConfig
    from sc2bench_env.versus.match import VersusMatch

    state = {}

    async def collect(act):
        if state.get("end") and act.ai.player_id == 1 and not state.get("shown"):
            await act.ai.client.debug_show_map()
            state["shown"] = True
        elif state.get("end") and act.ai.player_id == 1 and not state.get("ended"):
            tags = [unit.tag for unit in act.ai.enemy_structures]
            state["killed_n"] = len(tags)
            if tags:
                await act.ai.client.debug_kill_unit(tags)
                state["ended"] = True
        _ = UnitTypeId

    _install(monkeypatch, collect)
    match = VersusMatch(record_trajectory=True)
    try:
        config = EpisodeConfig(
            race="protoss", enemy_race="zerg", opponent="veryeasy",
            game_time_limit_seconds=90, blocking_decisions=True,
        )
        match.reset(config)
        first = match.wait_next()
        assert first is not None
        opened = match.request(first).observation.game.game_time_seconds
        assert play(match, first, [_wait(3)]) is False
        second = match.wait_next()
        assert second is not None and second != first
        held = match.request(second).observation.game.game_time_seconds
        assert held < opened + 1.5
        assert play(match, second, [_wait(3)]) is False
        later = match.wait_next()
        assert later is not None
        moved = match.request(later).observation.game.game_time_seconds
        assert moved >= 2.5

        state["end"] = True
        assert play(match, later, [_wait(2)]) is False
        for _ in range(12):
            player = match.wait_next()
            if player is None:
                break
            play(match, player, [_wait(2)])
        else:
            raise AssertionError({"killed_n": state.get("killed_n"), "shown": state.get("shown")})
        outcome = match.outcome()
        reasons = [row.get("end_reason") for row in outcome["players"]]
        results = [row.get("result") for row in outcome["players"]]
        assert reasons == ["game_ended", "game_ended"]
        assert "Result.Victory" in results and "Result.Defeat" in results
        replay = os.path.join(outcome["record_dir"], "replay.SC2Replay")
        assert os.path.isfile(replay)
    finally:
        match.close()
