"""Same-unit deployment/recovery and real long-distance jump arrival.

Debug changes only enemy scenarios and one low-health prerequisite. Own units
train normally through platform actions; tests never issue morph/jump/move.
"""
import os

import pytest

from tests.e2e.test_expanded_micro_e2e import _build_ready, _install_debug_adapter, _train_one, _wait
from tests.helpers.obs_invariants import assert_obs_consistent
from tests.helpers.skill_effects import ObservedFormRoundTrip, ObservedTeleport

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def _chain(name):
    buildings = ["supply_depot", "barracks", "factory"]
    if name in {"siege_tank", "thor"}:
        buildings += ["factory_techlab"]
    if name == "thor":
        buildings += ["armory"]
    if name in {"viking", "liberator", "battlecruiser"}:
        buildings += ["starport"]
    if name == "battlecruiser":
        buildings += ["starport_techlab", "fusion_core"]
    return buildings


@pytest.mark.parametrize("name", ["widow_mine", "liberator", "viking", "thor", "siege_tank"])
def test_e2e_same_unit_form_round_trip(monkeypatch, name):
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig
    from sc2.ids.unit_typeid import UnitTypeId

    base, deployed = {
        "widow_mine": (UnitTypeId.WIDOWMINE, UnitTypeId.WIDOWMINEBURROWED),
        "liberator": (UnitTypeId.LIBERATOR, UnitTypeId.LIBERATORAG),
        "viking": (UnitTypeId.VIKINGFIGHTER, UnitTypeId.VIKINGASSAULT),
        "thor": (UnitTypeId.THOR, UnitTypeId.THORAP),
        "siege_tank": (UnitTypeId.SIEGETANK, UnitTypeId.SIEGETANKSIEGED),
    }[name]
    tracker = ObservedFormRoundTrip(base, deployed)
    state = {"phase": 0, "prepared": 0, "samples": []}

    async def setup(act):
        if not state["phase"]:
            return
        units = act.ai.units.of_type({base, deployed})
        if not units:
            return
        unit = next((u for u in units if tracker.tag is None or u.tag == tracker.tag), None)
        if unit is None:
            return
        tracker.sample(unit.tag, unit.type_id)
        state["samples"].append((round(act.ai.time, 2), unit.tag, unit.type_id.name))
        if len(state["samples"]) > 15:
            del state["samples"][5]
        if state["prepared"] == state["phase"]:
            return
        enemies = act.ai.all_enemy_units.filter(lambda e: not e.is_memory and not e.is_snapshot)
        if enemies:
            await act.ai.client.debug_kill_unit(enemies)
        enemy_type = UnitTypeId.OVERLORD if name == "thor" else UnitTypeId.ULTRALISK
        if name == "siege_tank":
            # Passive stationary ground target stays inside the siege band
            # while Sharpy's per-tank deployment delay elapses.
            enemy_type = UnitTypeId.PYLON
        if state["phase"] == 1 or name == "thor":
            if state["phase"] == 2:
                enemy_type = UnitTypeId.MUTALISK
            point = unit.position.towards(act.ai.enemy_start_locations[0], 6)
            await act.ai.client.debug_create_unit([[enemy_type, 1, point, 2]])
        state["prepared"] = state["phase"]

    _install_debug_adapter(monkeypatch, setup)
    env = Environment("sharpy")
    try:
        env.reset(EpisodeConfig(decision_interval_seconds=2, game_time_limit_seconds=900,
                                opponent="builtin_veryeasy"))
        for building in _chain(name):
            _build_ready(env, building)
        _train_one(env, name)
        state["phase"] = 1
        obs, feedback, terminated, _ = env.step([
            {"action": "combat", "style": "defend", "target": "zone_0", "units": {name: 1}},
            _wait(.5),
        ])
        assert feedback.receipts[0].result == "accepted", feedback.receipts
        for phase, required_stage in ((1, 1), (2, 2)):
            state["phase"] = phase
            for _ in range(40):
                obs, _, terminated, _ = env.step([_wait(.5)])
                assert_obs_consistent(obs)
                assert obs.units.get(name) == 1 and obs.own_forces.assigned.get(name) == 1, obs
                if tracker.stage >= required_stage or terminated:
                    break
            assert not terminated and tracker.stage >= required_stage, state
        assert tracker.stage == 2 and tracker.tag is not None, state
    finally:
        state["phase"] = 0
        env.close()


def test_e2e_battlecruiser_actual_jump_arrival(monkeypatch):
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig
    from sc2.ids.unit_typeid import UnitTypeId

    state = {"phase": 0, "armed": False, "tracker": None, "samples": []}

    async def setup(act):
        if not state["phase"]:
            return
        units = act.ai.units(UnitTypeId.BATTLECRUISER)
        if not units:
            return
        unit = units.first
        if state["tracker"] is None:
            state["tracker"] = ObservedTeleport(tuple(act.ai.start_location))
        destination = None
        for order in unit.orders:
            if "TACTICALJUMP" in str(order.ability.id) and hasattr(order.target, "x"):
                destination = (order.target.x, order.target.y)
                break
        state["tracker"].sample(act.ai.time, unit.tag, tuple(unit.position), destination)
        state["samples"].append((round(act.ai.time, 2), unit.tag, tuple(unit.position), destination, unit.health))
        if len(state["samples"]) > 15:
            del state["samples"][5]
        if not state["armed"] and unit.distance_to(act.ai.start_location) >= 32:
            # Legitimate trigger after the platform-controlled march. No debug
            # relocation and no jump command from test code.
            await act.ai.client.debug_set_unit_value([unit.tag], 2, 40)
            state["armed"] = True

    _install_debug_adapter(monkeypatch, setup)
    env = Environment("sharpy")
    try:
        obs = env.reset(EpisodeConfig(decision_interval_seconds=2, game_time_limit_seconds=900,
                                      opponent="builtin_veryeasy"))
        for building in _chain("battlecruiser"):
            _build_ready(env, building)
        _train_one(env, "battlecruiser")
        assert len(obs.zones) >= 5, obs.zones
        state["phase"] = 1
        obs, feedback, terminated, _ = env.step([
            {"action": "combat", "style": "attack", "target": "zone_4", "units": {"battlecruiser": 1}},
            _wait(.5),
        ])
        assert feedback.receipts[0].result == "accepted", feedback.receipts
        for _ in range(120):
            obs, _, terminated, _ = env.step([_wait(.5)])
            assert_obs_consistent(obs)
            if (state["tracker"] is not None and state["tracker"].arrived) or terminated:
                break
        assert not terminated and state["armed"] and state["tracker"].arrived, state
        assert obs.units.get("battlecruiser") == 1 and obs.own_forces.assigned.get("battlecruiser") == 1, obs
    finally:
        state["phase"] = 0
        env.close()
