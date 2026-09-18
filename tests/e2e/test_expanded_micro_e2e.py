"""Real SC2 acceptance for expanded Terran combat skills and morphs.

Debug resources / fast-build only accelerate production. Units still train
through the platform path, and skill assertions require observed forms/orders.

Enable with:
  set SC2BENCH_E2E=1
  pytest tests/e2e/test_expanded_micro_e2e.py -q
"""

from __future__ import annotations

import os
from typing import Dict

import pytest

from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)

# Every item must pass independently. Order evidence proves activation only,
# not a completed hit; Raven hit requirements explicitly use enemy buffs.
SKILL_REQUIREMENTS = (
    "ghost_cloaked", "ghost_emp", "ghost_snipe", "cyclone_lock",
    "raven_matrix_hit", "raven_antiarmor_hit", "raven_turret",
    "thor_ap", "widow_mine_burrowed", "viking_assault", "liberator_ag",
    "bc_yamato", "bc_jump",
)


def _missing_skill_requirements(evidence):
    return [key for key in SKILL_REQUIREMENTS if int(evidence.get(key, 0)) <= 0]


def _wait(seconds: float = 8.0) -> dict:
    return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}


def _install_debug_adapter(monkeypatch, scenario_setup=None):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths

    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.position import Point2
    from sc2bench_env.backends.sharpy.races import terran
    from sharpy.plans import BuildOrder
    from sharpy.plans.acts import ActBase

    class DebugSetup(ActBase):
        done = False

        def __init__(self):
            super().__init__()
            self._spawn_ground = False
            self._spawn_overwhelm = False
            self._spawned_ground = False
            self._spawned_overwhelm = False

        async def execute(self):
            if self.done and (self.ai.minerals < 1000 or self.ai.vespene < 1000):
                await self.ai.client.debug_all_resources()
            if not self.done:
                await self.ai.client.debug_all_resources()
                await self.ai.client.debug_fast_build()
                self.done = True
            if scenario_setup is not None:
                await scenario_setup(self)
            own = Point2(self.ai.start_location)
            enemy_start = Point2(self.ai.enemy_start_locations[0])
            # Keep fodder near the enemy base so skills fire on approach,
            # not on top of our unfinished production.
            near_enemy = enemy_start.towards(own, 18)
            air = near_enemy.towards(own, -4)
            if self._spawn_ground and not self._spawned_ground:
                await self.ai.client.debug_create_unit(
                    [
                        [UnitTypeId.MARINE, 10, near_enemy, 2],
                        [UnitTypeId.MARAUDER, 2, near_enemy, 2],
                        [UnitTypeId.SIEGETANK, 2, near_enemy, 2],
                        [UnitTypeId.VIKINGFIGHTER, 3, air, 2],
                        [UnitTypeId.MEDIVAC, 1, near_enemy, 2],
                        # Close pack so Cyclone/Raven/Thor skills fire before the
                        # long march to the enemy natural finishes.
                        [UnitTypeId.MARINE, 6, own.towards(enemy_start, 12), 2],
                        [UnitTypeId.SIEGETANK, 1, own.towards(enemy_start, 12), 2],
                        [UnitTypeId.VIKINGFIGHTER, 2, own.towards(enemy_start, 14), 2],
                    ]
                )
                self._spawned_ground = True
                self._spawn_ground = False
            if self._spawn_overwhelm and not self._spawned_overwhelm:
                # Drop the overwhelm pack on the far side so a raiding harass
                # force meets it locally (task retreat uses local power only).
                front = enemy_start.towards(own, 10)
                await self.ai.client.debug_create_unit(
                    [
                        [UnitTypeId.MARINE, 30, front, 2],
                        [UnitTypeId.SIEGETANK, 6, front, 2],
                        [UnitTypeId.THOR, 3, front, 2],
                        [UnitTypeId.VIKINGFIGHTER, 8, front, 2],
                        [UnitTypeId.BATTLECRUISER, 2, front, 2],
                    ]
                )
                self._spawned_overwhelm = True
                self._spawn_overwhelm = False
            return True
        def request_skill_fodder(self) -> None:
            self._spawn_ground = True

        def request_overwhelm(self) -> None:
            self._spawn_overwhelm = True

    class TestAdapter(terran.TerranAdapter):
        def __init__(self):
            super().__init__()
            self.debug_setup = DebugSetup()

        def create_tactics(self):
            return BuildOrder([self.debug_setup, super().create_tactics()])

    adapter = TestAdapter()
    monkeypatch.setattr(terran, "get_adapter", lambda race: adapter)
    return adapter


def _wait_until(env, pred, *, max_steps: int = 40):
    obs = None
    terminated = False
    for _ in range(max_steps):
        obs, _, terminated, _ = env.step([_wait()])
        assert_obs_consistent(obs)
        if pred(obs) or terminated:
            return obs, terminated
    return obs, terminated


def _build_ready(env, target: str, *, max_steps: int = 25) -> None:
    env.step([{"action": "build", "target": target}, _wait(6)])
    obs, terminated = _wait_until(
        env, lambda o: o.buildings.get(target, 0) >= 1, max_steps=max_steps
    )
    assert not terminated, f"episode ended before {target}"
    assert obs.buildings.get(target, 0) >= 1, target


def _train_one(env, target: str, *, max_steps: int = 20) -> None:
    obs, feedback, _, _ = env.step(
        [{"action": "train", "target": target, "count": 1}, _wait(6)]
    )
    assert feedback.receipts[0].result == "accepted", target
    obs, terminated = _wait_until(
        env,
        lambda o: o.units.get(target, 0) >= 1
        or sum(
            int(d.produced)
            for d in env.task_manager.demands.values()
            if d.action == "train" and d.target == target
        )
        >= 1,
        max_steps=max_steps,
    )
    assert not terminated
    assert (
        obs.units.get(target, 0) >= 1
        or sum(
            int(d.produced)
            for d in env.task_manager.demands.values()
            if d.action == "train" and d.target == target
        )
        >= 1
    ), target


def _research_done(env, target: str, *, max_steps: int = 25) -> None:
    obs, feedback, _, _ = env.step(
        [{"action": "research", "target": target}, _wait(6)]
    )
    assert feedback.receipts[0].result in {"accepted", "idempotent_noop"}, target
    obs, terminated = _wait_until(
        env,
        lambda o: target in (o.upgrades or []) or o.research.get(target) == "completed",
        max_steps=max_steps,
    )
    assert not terminated
    assert target in (obs.upgrades or []) or obs.research.get(target) == "completed", target


def _merge_evidence(obs) -> Dict[str, int]:
    merged: Dict[str, int] = {}
    for row in (obs.combat or {}).values():
        for key, value in dict(row.get("skill_evidence") or {}).items():
            merged[key] = max(int(merged.get(key, 0)), int(value))
        for key, value in dict(row.get("forms") or {}).items():
            merged[key] = max(int(merged.get(key, 0)), int(value))
        for key, value in dict(row.get("cloaked") or {}).items():
            if int(value) > 0:
                merged[f"{key}_cloaked"] = max(
                    int(merged.get(f"{key}_cloaked", 0)), int(value)
                )
    return merged


def _enemy_ish_zone(obs) -> str:
    zones = list(obs.zones or [])
    assert zones
    return zones[min(4, len(zones) - 1)]


def _collect_expanded_skills_and_forms(monkeypatch) -> Dict[str, int]:
    """One live scenario supplies separately reported skill acceptance cases."""
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    adapter = _install_debug_adapter(monkeypatch)
    env = Environment("sharpy")
    try:
        env.reset(
            EpisodeConfig(
                decision_interval_seconds=6.0,
                game_time_limit_seconds=900.0,
                opponent="builtin_veryeasy",
            )
        )
        for building in (
            "supply_depot",
            "barracks",
            "factory",
            "starport",
            "armory",
            "ghost_academy",
            "fusion_core",
            "barracks_techlab",
            "factory_techlab",
            "starport_techlab",
        ):
            _build_ready(env, building)
        for _ in range(4):
            env.step([{"action": "build", "target": "supply_depot"}, _wait(4)])

        for upgrade in (
            "personal_cloaking",
            "yamato_cannon",
            "interference_matrix",
            "drilling_claws",
        ):
            _research_done(env, upgrade)

        for unit, count in (
            ("ghost", 1),
            ("cyclone", 2),
            ("raven", 2),
            ("thor", 1),
            ("widow_mine", 1),
            ("viking", 1),
            ("liberator", 1),
            ("battlecruiser", 1),
        ):
            for _ in range(count):
                _train_one(env, unit)

        obs, terminated = _wait_until(
            env,
            lambda o: all(
                int(o.units.get(name, 0)) >= need
                for name, need in (
                    ("ghost", 1),
                    ("cyclone", 2),
                    ("raven", 2),
                    ("thor", 1),
                    ("widow_mine", 1),
                    ("viking", 1),
                    ("liberator", 1),
                    ("battlecruiser", 1),
                )
            ),
            max_steps=35,
        )
        assert not terminated

        target = _enemy_ish_zone(obs)
        units = {}
        for name, need in (
            ("ghost", 1),
            ("cyclone", 2),
            ("raven", 2),
            ("thor", 1),
            ("widow_mine", 1),
            ("viking", 1),
            ("liberator", 1),
            ("battlecruiser", 1),
        ):
            free = int(obs.own_forces.free.get(name, 0))
            assert free >= need, f"required combat unit unavailable: {name} requested={need} free={free}"
            units[name] = need
        obs, feedback, terminated, _ = env.step(
            [
                {
                    "action": "combat",
                    "style": "attack",
                    "target": target,
                    "units": units,
                },
                _wait(6),
            ]
        )
        assert feedback.receipts[0].result == "accepted", feedback.receipts[0]
        assert not terminated, "episode ended before combat could start"
        assert obs.combat, f"combat missing after accept: {feedback.receipts}"

        # Spawn fodder after the mission is bound so evidence is collected live.
        adapter.debug_setup.request_skill_fodder()

        evidence: Dict[str, int] = _merge_evidence(obs)
        for _ in range(40):
            obs, _, terminated, _ = env.step([_wait(3)])
            assert_obs_consistent(obs)
            for key, value in _merge_evidence(obs).items():
                evidence[key] = max(int(evidence.get(key, 0)), int(value))
            if terminated or not _missing_skill_requirements(evidence):
                break
        return evidence
    finally:
        env.close()


@pytest.fixture(scope="module")
def expanded_skill_evidence():
    # One game, independent assertions. A missing first skill does not prevent
    # the other results from being reported; no 13-game duplicate setup.
    with pytest.MonkeyPatch.context() as monkeypatch:
        return _collect_expanded_skills_and_forms(monkeypatch)


@pytest.mark.parametrize("skill", SKILL_REQUIREMENTS)
def test_e2e_expanded_skill_and_form(skill, expanded_skill_evidence):
    assert skill not in _missing_skill_requirements(expanded_skill_evidence), (
        f"missing observed skill/form: {skill}; evidence={expanded_skill_evidence}"
    )


def test_e2e_mixed_composition_retreat(monkeypatch) -> None:
    """Mixed new-unit harass must end objectively and release assigned units."""
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    adapter = _install_debug_adapter(monkeypatch)
    env = Environment("sharpy")
    try:
        env.reset(
            EpisodeConfig(
                decision_interval_seconds=6.0,
                game_time_limit_seconds=720.0,
                opponent="builtin_veryeasy",
            )
        )
        for building in (
            "supply_depot",
            "barracks",
            "factory",
            "starport",
            "armory",
            "ghost_academy",
            "barracks_techlab",
            "factory_techlab",
            "starport_techlab",
        ):
            _build_ready(env, building)
        for _ in range(3):
            env.step([{"action": "build", "target": "supply_depot"}, _wait(4)])
        _research_done(env, "personal_cloaking")
        for unit in ("ghost", "cyclone", "widow_mine", "liberator", "marine"):
            _train_one(env, unit)

        obs, terminated = _wait_until(
            env,
            lambda o: all(
                int(o.units.get(n, 0)) >= 1
                for n in ("ghost", "cyclone", "widow_mine", "liberator", "marine")
            ),
            max_steps=25,
        )
        assert not terminated

        adapter.debug_setup.request_overwhelm()
        env.step([_wait(4)])

        target = _enemy_ish_zone(obs)
        units = {name: 1 for name in ("ghost", "cyclone", "widow_mine", "liberator", "marine")}
        assert all(int(obs.own_forces.free.get(name, 0)) >= 1 for name in units), obs.own_forces.free
        obs, feedback, _, _ = env.step(
            [
                {
                    "action": "combat",
                    "style": "attack",
                    "target": target,
                    "units": units,
                },
                _wait(6),
            ]
        )
        assert feedback.receipts[0].result == "accepted"

        # Let the harass reach the target, then drop overwhelm on that front.
        for _ in range(4):
            obs, _, terminated, _ = env.step([_wait(6)])
            if terminated:
                break
        adapter.debug_setup.request_overwhelm()

        ended = None
        for _ in range(40):
            obs, _, terminated, _ = env.step([_wait(6)])
            assert_obs_consistent(obs)
            for event in reversed(list(obs.recent_events or [])):
                if event.get("type") == "combat_ended":
                    ended = dict(event)
                    break
            if ended is not None or terminated:
                break

        assert ended is not None, (
            f"expected combat_ended, combat={obs.combat!r} events={obs.recent_events!r}"
        )
        assert ended.get("end_reason") == "withdrawn", ended
        assert any(int(obs.units.get(name, 0)) > 0 for name in units), "retreat requires survivors"
        assert int(obs.own_forces.assigned.get("ghost", 0)) == 0
        assert int(obs.own_forces.assigned.get("cyclone", 0)) == 0
        assert int(obs.own_forces.assigned.get("widow_mine", 0)) == 0
        assert int(obs.own_forces.assigned.get("liberator", 0)) == 0
        assert int(obs.own_forces.assigned.get("marine", 0)) == 0
        assert not any(
            (row.get("forms") or {}).get("widow_mine_burrowed")
            or (row.get("forms") or {}).get("liberator_ag")
            for row in (obs.combat or {}).values()
        )
    finally:
        env.close()
