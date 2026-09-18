"""FakeBackend scout observation and route-replace coverage."""

from __future__ import annotations

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.config import EpisodeConfig


def _wait(seconds: float = 2.0) -> dict:
    return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}


def test_fake_scout_exposes_scouting_and_advances_waypoints() -> None:
    env = Environment(FakeBackend())
    env.reset(EpisodeConfig(decision_interval_seconds=2.0, game_time_limit_seconds=120.0))
    route = ["zone_2", "zone_5", "zone_8"]
    obs, feedback, _, _ = env.step([{"action": "scout", "route": route}, _wait(2)])
    assert feedback.receipts[0].result == "accepted"
    assert "scv" in obs.scouting
    assert obs.scouting["scv"]["route"] == route
    assert obs.scouting["scv"]["status"] in {"in_progress", "waiting_to_start"}
    assert obs.scouting["scv"].get("moving_to") in set(route)
    assert obs.scouting["scv"].get("assigned") is True

    saw_second = False
    for _ in range(12):
        obs, _, _, _ = env.step([_wait(2)])
        row = obs.scouting.get("scv")
        if row and row.get("moving_to") == "zone_5":
            saw_second = True
            break
        if not row:
            break
    assert saw_second or "scv" not in obs.scouting

    obs, feedback, _, _ = env.step(
        [{"action": "scout", "route": ["zone_1"]}, _wait(1)]
    )
    assert feedback.receipts[0].result == "accepted"
    active = [d for d in env.task_manager.active_demands() if d.action == "scout"]
    assert len(active) == 1
    assert list(active[0].route or ()) == ["zone_1"]
    assert obs.scouting["scv"]["route"] == ["zone_1"]
    env.close()
