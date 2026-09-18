"""One natural-production mission lifecycle, not per-unit micro acceptance."""
import os

import pytest

from tests.e2e.test_combat_e2e import _build_ready, _wait
from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def test_loaded_retarget_return_and_redispatch(monkeypatch, tmp_path):
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2bench_env.backends.sharpy.acts import ActCombatMission

    frames = {}
    original = ActCombatMission.execute

    async def observe(act):
        result = await original(act)
        if act._bound:
            frames[id(act)] = {
                "tags": set(act._tags), "target": act.zone_id,
                "loaded": dict(act.loaded_counts), "end_reason": act.end_reason,
                "distance": max((u.distance_to(act.ai.start_location)
                                 for u in act.ai.units if u.tag in act._tags), default=0),
            }
        return result

    monkeypatch.setattr(ActCombatMission, "execute", observe)
    env = Environment("sharpy", record_dir=tmp_path)

    def until(predicate, steps=90, seconds=1):
        for _ in range(steps):
            obs, _, ended, _ = env.step([_wait(seconds)])
            assert not ended, "episode ended before lifecycle checkpoint"
            assert_obs_consistent(obs)
            if predicate(obs):
                return obs
        pytest.fail("lifecycle checkpoint did not occur within bounded waits")

    try:
        env.reset(EpisodeConfig(blocking_decisions=True, opponent="builtin_veryeasy",
                                game_time_limit_seconds=900, decision_interval_seconds=1))
        for target in ("supply_depot", "barracks", "refinery", "factory", "starport"):
            _build_ready(env, target, max_steps=35)
        env.step([{"action": "build", "target": "supply_depot"}, _wait(8)])
        env.step([{"action": "train", "target": "marine", "count": 4},
                  {"action": "train", "target": "medivac", "count": 1}, _wait(8)])
        obs = until(lambda o: o.own_forces.free.get("marine", 0) == 4
                    and o.own_forces.free.get("medivac", 0) == 1, seconds=4)
        first = next(z["zone_id"] for z in obs.zone_state if z["zone_role"] == "enemy_main")
        second = next(z["zone_id"] for z in obs.zone_state if z["zone_role"] == "enemy_natural")
        natural = next(z["zone_id"] for z in obs.zone_state if z["zone_role"] == "own_natural")
        obs, feedback, ended, _ = env.step([
            {"action": "combat", "style": "attack", "target": first,
             "units": {"marine": 4, "medivac": 1}}, _wait(1)])
        assert not ended and feedback.receipts[0].group == "group_1"
        assert feedback.receipts[0].result == "accepted"
        obs = until(lambda o: any(f["target"] == first and f["distance"] > 20
                                 and f["loaded"] == {"marine": 4} for f in frames.values()))
        controller_id = next(k for k, f in frames.items() if f["target"] == first)
        tags = set(frames[controller_id]["tags"])
        assert len(tags) == 5 and obs.own_forces.assigned == {"marine": 4, "medivac": 1}

        obs, feedback, ended, _ = env.step([
            {"action": "combat", "group": "group_1", "style": "defend", "target": second}, _wait(1)])
        assert not ended and feedback.receipts[0].result == "accepted"
        assert frames[controller_id]["target"] == second
        assert frames[controller_id]["tags"] == tags
        assert obs.combat["group_1"]["transport"]["loaded_units"] == {"marine": 4}

        obs, feedback, ended, _ = env.step([{"action": "retreat", "group": "group_1"}, _wait(1)])
        assert not ended and feedback.receipts[0].result == "accepted"
        obs = until(lambda o: "group_1" not in o.combat)
        assert frames[controller_id]["end_reason"] == "withdrawn"
        assert obs.combat["group_0"]["alive"] == {"marine": 4, "medivac": 1}
        assert obs.own_forces.free == {"marine": 4, "medivac": 1}
        assert not obs.own_forces.assigned
        assert any(e.get("group") == "group_1" and e.get("end_reason") == "withdrawn"
                   for e in obs.recent_events)

        obs, feedback, ended, _ = env.step([
            {"action": "combat", "style": "defend", "target": natural,
             "units": {"marine": 4, "medivac": 1}}, _wait(1)])
        assert not ended and feedback.receipts[0].result == "accepted"
        assert feedback.receipts[0].group == "group_2" and "group_1" not in obs.combat
        assert obs.combat["group_2"]["alive"] == {"marine": 4, "medivac": 1}
        assert not obs.own_forces.free and obs.own_forces.assigned == {"marine": 4, "medivac": 1}
        active = [f for f in frames.values() if f["target"] == natural and not f["end_reason"]]
        assert len(active) == 1 and active[0]["tags"] == tags
        assert_obs_consistent(obs)
    finally:
        env.close()
