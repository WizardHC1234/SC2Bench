"""Naturally produced groups: identity across updates and return to group_0."""
import os
import pytest
from tests.e2e.test_combat_e2e import _build_ready, _wait, _wait_until
from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
                                reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests")


@pytest.mark.parametrize("blocking", [True, False], ids=["blocking", "continuous"])
def test_real_groups_update_without_rebinding_then_return(monkeypatch, tmp_path, blocking):
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2bench_env.backends.sharpy.acts import ActCombatMission
    observed = {}
    original = ActCombatMission.execute

    async def observe(act):
        result = await original(act)
        if act._bound and not act.end_reason:
            observed[id(act)] = {"tags": set(act._tags), "target": act.zone_id,
                                 "phase": act.phase,
                                 "max_home_distance": max((u.distance_to(act.ai.start_location)
                                      for u in act.ai.units if u.tag in act._tags), default=0)}
        return result

    monkeypatch.setattr(ActCombatMission, "execute", observe)
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        obs = env.reset(EpisodeConfig(blocking_decisions=blocking, opponent="builtin_veryeasy",
                                      game_time_limit_seconds=900, decision_interval_seconds=4))
        _build_ready(env, "supply_depot")
        _build_ready(env, "barracks")
        env.step([{"action": "train", "target": "marine", "count": 4}, _wait(4)])
        obs, ended = _wait_until(env, lambda o: o.combat["group_0"]["alive"].get("marine", 0) >= 4)
        assert not ended
        home = next(z["zone_id"] for z in obs.zone_state if z["zone_role"] == "own_main")
        natural = next(z["zone_id"] for z in obs.zone_state if z["zone_role"] == "own_natural")
        obs, f, ended, _ = env.step([
            {"action": "combat", "style": "attack", "target": "zone_4", "units": {"marine": 2}},
            {"action": "combat", "style": "defend", "target": home, "units": {"marine": 1}}, _wait(8)])
        assert [r.group for r in f.receipts] == ["group_1", "group_2"]
        assert all(r.result == "accepted" for r in f.receipts)
        obs, ended = _wait_until(env, lambda o: any(r["target"] == "zone_4" and r["max_home_distance"] > 20
                                                    for r in observed.values()))
        assert not ended
        first_id = next(k for k, r in observed.items() if r["target"] == "zone_4")
        tags = set(observed[first_id]["tags"])
        assert len(tags) == 2
        obs, f, ended, _ = env.step([
            {"action": "combat", "group": "group_1", "style": "defend", "target": natural}, _wait(8)])
        assert f.receipts[0].result == "accepted"
        assert set(observed[first_id]["tags"]) == tags
        assert observed[first_id]["target"] == natural
        assert obs.combat["group_1"]["alive"] == {"marine": 2}
        assert obs.combat["group_2"]["target"] == home  # home defense must not merge it
        obs, f, ended, _ = env.step([{"action": "retreat", "group": "group_1"}, _wait(4)])
        assert f.receipts[0].result == "accepted"
        obs, ended = _wait_until(env, lambda o: "group_1" not in o.combat)
        assert not ended
        assert obs.combat["group_0"]["alive"] == {"marine": 3}
        assert obs.combat["group_2"]["alive"] == {"marine": 1}
        assert obs.own_forces.assigned == {"marine": 1}
        assert obs.own_forces.free == {"marine": 3}
        assert any(e.get("group") == "group_1" and e.get("end_reason") == "withdrawn" for e in obs.recent_events)
        assert_obs_consistent(obs)
        obs, f, ended, _ = env.step([
            {"action": "combat", "style": "defend", "target": natural, "units": {"marine": 3}}, _wait(4)])
        assert not ended and f.receipts[0].result == "accepted"
        assert f.receipts[0].group == "group_3"  # Ended identities are not reused.
        assert "group_1" not in obs.combat
        assert obs.combat["group_3"]["alive"] == {"marine": 3}
        assert obs.combat["group_2"]["alive"] == {"marine": 1}
        assert obs.own_forces.assigned == {"marine": 4} and not obs.own_forces.free
        assert_obs_consistent(obs)
    finally:
        env.close()
