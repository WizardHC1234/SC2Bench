"""Mission-level retreat acceptance; no own-unit micro commands from tests."""
import os

import pytest

from tests.e2e.test_expanded_micro_e2e import (
    _build_ready, _install_debug_adapter, _train_one, _wait,
)
from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


@pytest.mark.parametrize("parallel_defend", [False, True], ids=["single_mission", "parallel_defend"])
def test_e2e_deployed_tank_mixed_force_withdraws_and_releases(monkeypatch, parallel_defend):
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.position import Point2

    state = {"active": False, "target": False, "threat": False,
             "tag": None, "deployed": False, "mobile_after": False,
             "home_after": False, "powered_threat": False, "samples": []}

    async def setup(act):
        if not state["active"]:
            return
        tanks = act.ai.units.of_type({UnitTypeId.SIEGETANK, UnitTypeId.SIEGETANKSIEGED})
        if not tanks:
            return
        tank = next((u for u in tanks if state["tag"] in {None, u.tag}), None)
        if tank is None:
            return
        state["tag"] = tank.tag
        distance = tank.distance_to(act.ai.start_location)
        state["samples"].append((round(act.ai.time, 2), tank.type_id.name, round(distance, 1)))
        if len(state["samples"]) > 20:
            del state["samples"][5]
        if not state["target"] and distance >= 24:
            await act.ai.client.debug_create_unit([
                [UnitTypeId.INFESTOR, 1, tank.position.towards(act.ai.enemy_start_locations[0], 9), 2]
            ])
            state["target"] = True
        # Keep the passive deployment prerequisite alive until deployment.
        if state["target"] and not state["threat"]:
            for enemy in act.ai.enemy_units(UnitTypeId.INFESTOR):
                await act.ai.client.debug_set_unit_value([enemy.tag], 2, enemy.health_max)
                await act.ai.client.debug_set_unit_value([enemy.tag], 1, 0)
        if state["threat"]:
            state["powered_threat"] |= any(
                enemy.is_powered for enemy in act.ai.enemy_structures(UnitTypeId.PHOTONCANNON)
            )
        if state["target"] and tank.type_id == UnitTypeId.SIEGETANKSIEGED and not state["threat"]:
            state["deployed"] = True
            enemies = act.ai.all_enemy_units.filter(lambda e: not e.is_memory and not e.is_snapshot)
            if enemies:
                await act.ai.client.debug_kill_unit(enemies)
            # Powered static threats outside their initial tank weapon range:
            # isolate recovery, not whether this small force survives BC pursuit.
            # Separate positions avoid a debug-spawn stack scattering onto us.
            front = tank.position.towards(act.ai.enemy_start_locations[0], 10)
            lateral = Point2((-(front.y - tank.position.y) / 10,
                              (front.x - tank.position.x) / 10))
            await act.ai.client.debug_create_unit([
                [UnitTypeId.PYLON, 1,
                 tank.position.towards(act.ai.enemy_start_locations[0], 14), 2],
                *[[UnitTypeId.PHOTONCANNON, 1, front + lateral * offset, 2]
                  for offset in (-4.5, -1.5, 1.5, 4.5)],
            ])
            state["threat"] = True
        elif state["threat"] and tank.type_id == UnitTypeId.SIEGETANK:
            state["mobile_after"] = True
            if distance <= 10:
                state["home_after"] = True

    _install_debug_adapter(monkeypatch, setup)
    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(decision_interval_seconds=2,
                                     game_time_limit_seconds=900,
                                     opponent="builtin_veryeasy"))
        for building in ("supply_depot", "barracks", "factory", "factory_techlab"):
            _build_ready(env, building)
        _train_one(env, "siege_tank")
        for _ in range(6 if parallel_defend else 4):
            _train_one(env, "marine")
        assert len(obs.zones) >= 5
        if parallel_defend:
            obs, feedback, terminated, _ = env.step([
                {"action": "combat", "style": "defend", "target": "zone_0",
                 "units": {"marine": 2}}, _wait(.5),
            ])
            assert not terminated and feedback.receipts[0].result == "accepted", feedback.receipts
            assert obs.own_forces.assigned.get("marine") == 2, obs.own_forces
        state["active"] = True
        obs, feedback, terminated, _ = env.step([
            {"action": "combat", "style": "attack", "target": "zone_4",
             "units": {"siege_tank": 1, "marine": 4}}, _wait(.5),
        ])
        assert feedback.receipts[0].result == "accepted", feedback.receipts
        ended = None
        withdrawing = False
        for _ in range(200):
            obs, _, terminated, _ = env.step([_wait(.5)])
            assert_obs_consistent(obs)
            for name in ("siege_tank", "marine"):
                total = obs.units.get(name, 0)
                assigned = obs.own_forces.assigned.get(name, 0)
                free = obs.own_forces.free.get(name, 0)
                assert 0 <= assigned <= total and 0 <= free <= total - assigned, obs.own_forces
            if parallel_defend:
                defenders = [row for name, row in obs.combat.items() if name != "group_0" and row.get("style") == "defend"]
                assert len(defenders) == 1 and defenders[0].get("alive", {}).get("marine") == 2, obs.combat
            withdrawing |= any(row.get("phase") == "withdrawing" for row in obs.combat.values())
            ended = next((event for event in reversed(obs.recent_events or [])
                          if event.get("type") == "combat_ended"), None)
            if ended or terminated:
                break
        assert not terminated and ended is not None, (state, obs.combat, obs.recent_events)
        assert ended.get("end_reason") == "withdrawn", (ended, state)
        assert (withdrawing and state["powered_threat"] and state["deployed"]
                and state["mobile_after"] and state["home_after"]), state
        assert obs.units.get("siege_tank") == 1, "the deployed tank must survive recovery"
        assert obs.own_forces.assigned.get("siege_tank", 0) == 0, obs.own_forces
        assert obs.own_forces.assigned.get("marine", 0) == (2 if parallel_defend else 0), obs.own_forces
        assert obs.own_forces.free.get("siege_tank") == 1, obs.own_forces
        # Released survivors can be assigned again, not merely counted as free.
        obs, feedback, terminated, _ = env.step([
            {"action": "combat", "style": "defend", "target": "zone_0",
             "units": {"siege_tank": 1}}, _wait(.5),
        ])
        assert not terminated and feedback.receipts[0].result == "accepted", feedback.receipts
        assert obs.own_forces.assigned.get("siege_tank") == 1, obs.own_forces
    finally:
        state["active"] = False
        env.close()
