"""Debug preconditions, actual add-on actions/Obs/records; no LLM calls."""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def test_e2e_single_addon_cumulative_and_mixed_requests(monkeypatch, tmp_path):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId as U
    from sharpy.plans import BuildOrder
    from sharpy.plans.acts import ActBase
    from sc2bench_env.backends.sharpy.races import terran
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig
    from sc2bench_env.recording.reader import read_episode

    hosts = [U.BARRACKS] * 3 + [U.FACTORY] * 3 + [U.STARPORT] * 3
    state = {"created": 0, "funded": False, "attachments": [], "host_points": []}

    class Setup(ActBase):
        async def execute(self):
            state["attachments"] = [(parent.tag, parent.add_on_tag) for parent in self.ai.structures
                                    if parent.type_id in hosts and parent.add_on_tag]
            if not state["funded"]:
                await self.ai.client.debug_all_resources()
                state["funded"] = True
            if state["created"] < len(hosts):
                kind = hosts[state["created"]]
                point = None
                near = self.ai.start_location
                for dx in range(-54, 55, 9):
                    for dy in range(-54, 55, 9):
                        candidate = near.offset((dx, dy))
                        if any(candidate.distance_to(old) < 8 for old in state["host_points"]):
                            continue
                        legal = await self.ai.find_placement(kind, candidate, max_distance=0,
                                                            random_alternative=False)
                        if legal is not None and await self.ai.find_placement(
                                U.SUPPLYDEPOT, candidate.offset((2.5, -0.5)), max_distance=0,
                                random_alternative=False) is not None:
                            point = legal
                            break
                    if point is not None:
                        break
                if point is not None:
                    await self.ai.client.debug_create_unit([[kind, 1, point, self.ai.player_id]])
                    state["host_points"].append(point)
                    state["created"] += 1
            return True

    original = terran.TerranAdapter.create_tactics
    monkeypatch.setattr(terran.TerranAdapter, "create_tactics",
                        lambda adapter: BuildOrder([Setup(), original(adapter)]))
    env = Environment("sharpy", record_dir=tmp_path / "records")

    def wait(seconds=2):
        return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}

    def advance(predicate, steps=30):
        for _ in range(steps):
            obs, _, done, _ = env.step([wait()])
            assert not done
            if predicate(obs):
                return obs
        raise AssertionError(f"fixture did not reach expected observed state: {state['created']} hosts")

    expected = {"barracks_techlab": 1, "factory_techlab": 2,
                "starport_techlab": 1, "starport_reactor": 1}
    try:
        env.reset(EpisodeConfig(opponent="builtin_veryeasy", game_time_limit_seconds=240,
                               decision_interval_seconds=2))
        advance(lambda obs: all(obs.buildings.get(name, 0) == 3 for name in ("barracks", "factory", "starport")))
        _, feedback, _, _ = env.step([{"action": "build", "target": "barracks_techlab"}, wait()])
        assert feedback.receipts[0].result == "accepted"
        obs = advance(lambda obs: obs.buildings.get("barracks_techlab", 0) >= 1)
        obs, _, _, _ = env.step([wait(5)])
        assert obs.buildings["barracks_techlab"] == 1  # three hosts must not receive three labs
        _, feedback, _, _ = env.step([
            {"action": "build", "target": "factory_techlab"},
            {"action": "build", "target": "factory_techlab"}, wait()])
        assert all(r.result == "accepted" for r in feedback.receipts[:2])
        advance(lambda obs: obs.buildings.get("factory_techlab", 0) >= 2)
        _, feedback, _, _ = env.step([
            {"action": "build", "target": "starport_techlab"},
            {"action": "build", "target": "starport_reactor"}, wait()])
        assert all(r.result == "accepted" for r in feedback.receipts[:2])
        advance(lambda obs: all(obs.buildings.get(name, 0) >= count for name, count in expected.items()))
        obs, _, done, _ = env.step([wait(8)])
        assert not done
        for name, count in expected.items():
            assert obs.buildings[name] == count
            assert obs.building[name]["completed"] == count
            demands = [d for d in env.task_manager.demands.values() if d.action == "build" and d.target == name]
            assert len(demands) == count and all(d.produced == d.count == 1 for d in demands)
        # Confirm attachment and host uniqueness using private engine diagnostics.
        attachments = state["attachments"]
        assert len(attachments) == 5
        assert len({host for host, addon in attachments}) == len({addon for host, addon in attachments}) == 5
        directory = env.record_path
        env.close()
        restored = read_episode(directory)
        last = next(row for row in reversed(restored["steps"]) if row.get("type") == "step")
        for name, count in expected.items():
            assert last["observation"]["building"][name]["completed"] == count
        assert restored["summary"]["rejected_count"] == 0
        assert (directory / "replay.SC2Replay").stat().st_size > 0
    finally:
        env.close()
