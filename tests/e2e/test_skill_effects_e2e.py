"""Real impact and sustained/interrupted lock, not just activation orders."""
import os

import pytest

from tests.e2e.test_expanded_micro_e2e import (
    _build_ready, _install_debug_adapter, _research_done, _train_one, _wait,
)
from tests.helpers.obs_invariants import assert_obs_consistent
from tests.helpers.skill_effects import ObservedImpact, ObservedSustainedLock, ObservedLockCycle

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def _collect_effect(monkeypatch, skill):
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig
    from sc2.ids.buff_id import BuffId
    from sc2.ids.unit_typeid import UnitTypeId

    name, own_type, enemy_type, hint, minimum = {
        "snipe": ("ghost", UnitTypeId.GHOST, UnitTypeId.OVERLORD, "SNIPE", 100),
        "yamato": ("battlecruiser", UnitTypeId.BATTLECRUISER, UnitTypeId.ULTRALISK, "YAMATO", 200),
        "lock": ("cyclone", UnitTypeId.CYCLONE, UnitTypeId.OVERLORD, "LOCKON", 100),
        "lock_cycle": ("cyclone", UnitTypeId.CYCLONE, UnitTypeId.COMMANDCENTER, "LOCKON", 100),
    }[skill]
    impact = ObservedImpact(minimum)
    sustained = ObservedSustainedLock()
    cycle = ObservedLockCycle()
    state = {"phase": 0, "prepared": 0, "old_target": None,
             "old_cleared": False, "new_locked": False, "samples": []}

    async def setup(act):
        units = act.ai.units(own_type)
        phase = state["phase"]
        if not phase or not units:
            return
        unit = units.first
        if state["prepared"] != phase:
            enemies = act.ai.enemy_units.filter(lambda e: not e.is_memory and not e.is_snapshot)
            if enemies:
                await act.ai.client.debug_kill_unit(enemies)
            if phase == 1:
                await act.ai.client.debug_set_unit_value([unit.tag], 1, 200)
            point = unit.position.towards(act.ai.enemy_start_locations[0], 6)
            await act.ai.client.debug_create_unit([[enemy_type, 1, point, 2]])
            state["prepared"] = phase
            return  # Never interpret precondition mutations as skill impact.
        enemies = [e for e in act.ai.all_enemy_units if e.type_id == enemy_type
                   and not e.is_memory and not e.is_snapshot and e.is_visible]
        health = {e.tag: e.health for e in enemies}
        ordered = [o.target for o in unit.orders
                   if hint in str(o.ability.id) and isinstance(o.target, int)]
        impact.sample(act.ai.time, health, ordered)
        locked = [e.tag for e in enemies if e.has_buff(BuffId.LOCKON)]
        if phase == 1:
            sustained.sample(act.ai.time, health, locked)
            cycle.sample(act.ai.time, health, locked)
        else:
            old = state["old_target"]
            # Inspect the production micro's private state only in this test,
            # not in model Obs. A new engine lock alone is not cleanup proof.
            from sc2bench_env.backends.sharpy.terran_micro import MicroCyclone
            micro = None
            macro = act.ai._macro_tasks
            for task in macro:
                mission = task.get("_act")
                rules = getattr(mission, "_micro_rules", None)
                candidate = rules.unit_micros.get(own_type) if rules is not None else None
                if isinstance(candidate, MicroCyclone):
                    micro = candidate
                    break
            if micro is not None:
                remembered = list(micro.locks.values()) + list(micro.pending_locks.values())
                state["old_cleared"] = old not in health and all(row[0] != old for row in remembered)
            if state["old_cleared"] and any(tag != old for tag in locked):
                state["new_locked"] = True
        row = {"time": round(act.ai.time, 2), "phase": phase, "health": health,
               "orders": [str(o.ability.id) for o in unit.orders], "locked": locked}
        if len(state["samples"]) < 5:
            state["samples"].append(row)
        else:
            state["samples"].append(row)
            if len(state["samples"]) > 15:
                del state["samples"][5]

    _install_debug_adapter(monkeypatch, setup)
    env = Environment("sharpy")
    try:
        env.reset(EpisodeConfig(decision_interval_seconds=2, game_time_limit_seconds=900,
                                opponent="builtin_veryeasy"))
        chain = ["supply_depot", "barracks"]
        if name == "ghost":
            chain += ["ghost_academy", "barracks_techlab"]
        elif name == "cyclone":
            chain += ["factory", "factory_techlab"]
        else:
            chain += ["factory", "starport", "starport_techlab", "fusion_core"]
        for building in chain:
            _build_ready(env, building)
        if skill == "yamato":
            _research_done(env, "yamato_cannon")
        _train_one(env, name)
        state["phase"] = 1
        obs, feedback, terminated, _ = env.step([
            {"action": "combat", "style": "defend", "target": "zone_0", "units": {name: 1}},
            _wait(.5),
        ])
        assert feedback.receipts[0].result == "accepted", feedback.receipts
        for _ in range(60 if skill == "lock_cycle" else 40):
            obs, _, terminated, _ = env.step([_wait(.5)])
            assert_obs_consistent(obs)
            confirmed = cycle.completed if skill == "lock_cycle" else (
                sustained.sustained if skill == "lock" else impact.hits)
            if confirmed or terminated:
                break
        assert not terminated, state["samples"]
        if skill == "lock_cycle":
            assert cycle.completed, state["samples"]
        elif skill == "lock":
            assert sustained.sustained, state["samples"]
            state["old_target"] = next(iter(sustained.sustained))
            state["phase"] = 2
            for _ in range(40):
                obs, _, terminated, _ = env.step([_wait(.5)])
                assert_obs_consistent(obs)
                if state["new_locked"] or terminated:
                    break
            assert not terminated and state["old_cleared"] and state["new_locked"], state
        else:
            assert impact.hits, state["samples"]
        return env.record_path
    finally:
        state["phase"] = 0
        env.close()


@pytest.mark.parametrize("skill", ["snipe", "yamato", "lock", "lock_cycle"])
def test_e2e_observed_skill_effect(monkeypatch, skill):
    _collect_effect(monkeypatch, skill)
