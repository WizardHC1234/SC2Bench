"""Isolated legal skill targets; debug sets preconditions, never casts skills.

Own units/buildings/upgrades still use platform actions. Snapshots below are
test-only diagnostics of engine state, not proposed-command success counters.
"""
import os

import pytest

from tests.e2e.test_expanded_micro_e2e import (
    _build_ready, _install_debug_adapter, _merge_evidence,
    _research_done, _train_one, _wait,
)
from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)

GROUPS = {
    "ghost": ("ghost_emp", "ghost_snipe", "ghost_cloaked"),
    "cyclone": ("cyclone_lock",),
    "raven": ("raven_matrix_hit", "raven_antiarmor_hit", "raven_turret"),
    "battlecruiser": ("bc_jump",),
}


def _collect_group(monkeypatch, name):
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig
    from sc2.ids.buff_id import BuffId
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2bench_env.backends.sharpy.compat import has_antiarmor_debuff

    state = {"request": None, "last": None, "snapshots": {}, "effects": {}, "previous": None}
    types = {
        "ghost_emp": [(UnitTypeId.ARCHON, 1)],
        "ghost_snipe": [(UnitTypeId.ULTRALISK, 1)],
        "ghost_cloaked": [(UnitTypeId.MARINE, 1)],
        "cyclone_lock": [(UnitTypeId.OVERLORD, 1)],
        "raven_matrix_hit": [(UnitTypeId.SIEGETANK, 1)],
        # Passive, high-health targets survive until the missile detonates.
        # Marines can be killed by workers/turrets before it actually lands.
        "raven_antiarmor_hit": [(UnitTypeId.OVERLORD, 4)],
        "raven_turret": [(UnitTypeId.ZERGLING, 1)],
        "bc_jump": [(UnitTypeId.OVERLORD, 1)],
    }
    own_type = {
        "ghost": UnitTypeId.GHOST, "cyclone": UnitTypeId.CYCLONE,
        "raven": UnitTypeId.RAVEN, "battlecruiser": UnitTypeId.BATTLECRUISER,
    }[name]

    async def setup(act):
        skill = state["request"]
        units = act.ai.units(own_type)
        if skill is None or not units:
            return
        unit = units.first
        if state["last"] != skill:
            # Remove previous test targets. No engine spell/morph commands are
            # issued here; energy/life and idle enemy targets are prerequisites.
            enemies = act.ai.enemy_units.filter(lambda e: not e.is_memory and not e.is_snapshot)
            if enemies:
                await act.ai.client.debug_kill_unit(enemies)
            await act.ai.client.debug_set_unit_value([unit.tag], 1, 200)
            await act.ai.client.debug_set_unit_value([unit.tag], 2, unit.health_max)
            if skill == "bc_jump":
                await act.ai.client.debug_set_unit_value([unit.tag], 2, 40)
            point = unit.position.towards(act.ai.enemy_start_locations[0], 6)
            await act.ai.client.debug_create_unit(
                [[kind, count, point, 2] for kind, count in types[skill]]
            )
            state["last"] = skill
            state["previous"] = None
        abilities = act.cd_manager.available_dict.get(unit.tag, [])
        row = {
            "energy": round(unit.energy, 1), "health": round(unit.health, 1),
            "abilities": [a.name for a in abilities],
            "orders": [str(o.ability.id) for o in unit.orders],
            "enemies": [(e.type_id.name, round(e.distance_to(unit), 1), round(e.shield, 1),
                         list(e._proto.buff_ids))
                        for e in act.ai.enemy_units if not e.is_memory and not e.is_snapshot],
        }
        snapshots = state["snapshots"].setdefault(skill, [])
        if not snapshots or row != snapshots[-1]:
            snapshots.append(row)
            if len(snapshots) > 10:
                del snapshots[5]  # Preserve trigger start as well as final state.
        current = {e.tag: e.shield for e in act.ai.enemy_units
                   if not e.is_memory and not e.is_snapshot}
        previous = state["previous"]
        if skill == "ghost_emp" and previous is not None:
            energy, shields = previous
            if energy - unit.energy >= 70 and any(
                shields.get(tag, shield) - shield >= 90 for tag, shield in current.items()
            ):
                # Isolated Ghost: simultaneous >=75 energy and ~100 shield
                # removal distinguishes EMP from ordinary weapon fire.
                state["effects"]["ghost_emp_hit"] = 1
        state["previous"] = (unit.energy, current)
        # Fast effects can disappear between decisions. These are actual enemy
        # engine buffs, independent of micro cast attempts or mission labels.
        for enemy in act.ai.enemy_units:
            if enemy.is_memory or enemy.is_snapshot:
                continue
            if has_antiarmor_debuff(enemy, act.knowledge.version_manager.full_version):
                state["effects"]["raven_antiarmor_hit"] = 1
            for buff, key in (
                (BuffId.LOCKON, "cyclone_lock_hit"),
                (BuffId.RAVENSCRAMBLERMISSILE, "raven_matrix_hit"),
                (BuffId.RAVENSHREDDERMISSILEARMORREDUCTION, "raven_antiarmor_hit"),
            ):
                if enemy.has_buff(buff):
                    state["effects"][key] = 1

    _install_debug_adapter(monkeypatch, setup)
    env = Environment("sharpy")
    evidence = {}
    try:
        env.reset(EpisodeConfig(
            decision_interval_seconds=2, game_time_limit_seconds=900,
            opponent="builtin_veryeasy",
        ))
        chain = ["supply_depot", "barracks"]
        if name == "ghost":
            chain += ["ghost_academy", "barracks_techlab"]
        else:
            chain += ["factory"]
            if name == "cyclone":
                chain += ["factory_techlab"]
            else:
                chain += ["starport", "starport_techlab"]
                if name == "battlecruiser":
                    chain += ["fusion_core"]
        for building in chain:
            _build_ready(env, building)
        if name == "ghost":
            _research_done(env, "personal_cloaking")
        if name == "raven":
            _research_done(env, "interference_matrix")
        _train_one(env, name)
        obs, _, _, _ = env.step([_wait(1)])
        state["request"] = GROUPS[name][0]
        # Our main zone is zone_0. Defend keeps the spellcaster and isolated
        # targets local, without a long march or an overwhelming assault group.
        obs, feedback, terminated, _ = env.step([
            {"action": "combat", "style": "defend", "target": "zone_0", "units": {name: 1}},
            _wait(0.5),
        ])
        assert feedback.receipts[0].result == "accepted", feedback.receipts
        assert not terminated
        for skill in GROUPS[name]:
            state["request"] = skill
            for _ in range(30):
                obs, _, terminated, _ = env.step([_wait(0.5)])
                assert_obs_consistent(obs)
                for key, value in _merge_evidence(obs).items():
                    evidence[key] = max(evidence.get(key, 0), value)
                evidence.update(state["effects"])
                confirmed = evidence.get(skill, 0)
                if skill in {"ghost_emp", "cyclone_lock"}:
                    confirmed = max(confirmed, evidence.get(skill + "_hit", 0))
                if confirmed > 0 or terminated:
                    break
            assert not terminated, (name, skill, state["snapshots"])
        return evidence, state["snapshots"]
    finally:
        state["request"] = None
        env.close()


@pytest.fixture(scope="module", params=tuple(GROUPS))
def focused_group(request):
    with pytest.MonkeyPatch.context() as monkeypatch:
        return request.param, _collect_group(monkeypatch, request.param)


def test_e2e_focused_skill_group(focused_group):
    name, (evidence, snapshots) = focused_group
    missing = [skill for skill in GROUPS[name]
               if max(evidence.get(skill, 0), evidence.get(skill + "_hit", 0)) <= 0]
    if name == "cyclone" and not evidence.get("cyclone_lock_hit"):
        missing.append("cyclone_lock_hit")
    assert not missing, f"missing={missing}; evidence={evidence}; engine_snapshots={snapshots}"
