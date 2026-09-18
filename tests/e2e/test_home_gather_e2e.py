"""Observe naturally produced units actually arrive; no teleport/debug/micro."""
import os

import pytest

from tests.e2e.test_combat_e2e import _build_ready, _wait, _wait_until
from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


@pytest.mark.parametrize("blocking", [True, False], ids=["blocking", "continuous"])
def test_e2e_new_infantry_and_support_gather_without_joining_existing_mission(monkeypatch, tmp_path, blocking):
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2bench_env.backends.sharpy.gather import PlanHomeGather, HOME_GATHER_RADIUS

    samples = {}
    latest = {}
    original = PlanHomeGather.execute

    async def observe(act):
        point = act.home_point()
        latest["point"] = point
        for u in act.ai.units:
            name = act.adapter.normalize_unit_name(u.type_id.name)
            if name not in {"marine", "medivac"}:
                continue
            row = samples.setdefault(u.tag, {"name": name, "first": u.position,
                                              "max_distance": 0, "arrived": False})
            distance = u.distance_to(point)
            row["max_distance"] = max(row["max_distance"], distance)
            row["arrived"] |= distance <= HOME_GATHER_RADIUS
            row["position"] = u.position
            row["reserved"] = u.tag in getattr(act.ai, "bench_combat_tags", set())
            if row["reserved"]:
                assert not act.eligible(u), "gather must not pull a mission-bound unit"
        return await original(act)

    monkeypatch.setattr(PlanHomeGather, "execute", observe)
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(blocking_decisions=blocking, game_time_limit_seconds=900,
                                opponent="builtin_veryeasy", decision_interval_seconds=4))
        for target in ("supply_depot", "barracks", "refinery", "factory", "starport"):
            _build_ready(env, target)
        env.step([{"action": "build", "target": "supply_depot"},
                  {"action": "train", "target": "marine", "count": 2},
                  {"action": "train", "target": "medivac", "count": 1}, _wait(4)])
        obs, ended = _wait_until(env, lambda o: sum(r["arrived"] for r in samples.values()
                                                   if r["name"] == "marine") >= 2
                               and any(r["arrived"] for r in samples.values() if r["name"] == "medivac"),
                               max_steps=45)
        assert not ended and obs.units.get("marine", 0) == 2 and obs.units.get("medivac", 0) == 1
        assert obs.own_forces.free["marine"] == 2
        assert obs.own_forces.free["medivac"] == 1
        assert set(obs.combat) == {"group_0"}  # No outbound mission; home defaults to defense.
        assert any(r["arrived"] and r["max_distance"] > HOME_GATHER_RADIUS + 2
                   and r["first"].distance_to(r["position"]) > 2 for r in samples.values()), samples
        natural = next(z["zone_id"] for z in obs.zone_state if z["zone_role"] == "own_natural")
        obs, feedback, ended, _ = env.step([
            {"action": "combat", "style": "defend", "target": natural, "units": {"marine": 2}},
            {"action": "train", "target": "marine", "count": 1}, _wait(4)])
        assert feedback.receipts[0].result == "accepted"
        obs, ended = _wait_until(env, lambda o: o.units.get("marine", 0) == 3
                               and any(r["name"] == "marine" and not r.get("reserved") and r["arrived"]
                                       for r in samples.values()), max_steps=20)
        assert not ended
        for _ in range(3):
            obs, _, ended, _ = env.step([_wait(4)])
            assert not ended
            assert_obs_consistent(obs)
        row = next(r for name, r in obs.combat.items() if name != "group_0" and r["style"] == "defend")
        assert row["requested"] == {"marine": 2} and row["alive"] == {"marine": 2}
        assert obs.own_forces.assigned["marine"] == 2
        assert obs.own_forces.free["marine"] == 1
        assert any(r["name"] == "marine" and not r.get("reserved")
                   and r["position"].distance_to(latest["point"]) <= HOME_GATHER_RADIUS
                   for r in samples.values()), samples
    finally:
        env.close()
