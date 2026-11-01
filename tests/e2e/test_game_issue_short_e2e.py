"""Windows short games for the game-issue fix plan."""
import os

import pytest

from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.observations import render_observation_text
from sc2bench_env.recording.reader import read_episode
from tests.helpers.drive import drive

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)

_WORKERS = {"terran": "SCVs", "protoss": "Probes", "zerg": "Drones"}


@pytest.mark.parametrize("race", ("terran", "protoss", "zerg"))
def test_e2e_short_game_keeps_race_text_events_and_integrity(tmp_path, race):
    env = Environment("sharpy", record_dir=tmp_path)
    worker = _WORKERS[race]
    try:
        obs = env.reset(EpisodeConfig(
            race=race, opponent="veryeasy", game_time_limit_seconds=45,
        ))
        opening = render_observation_text(obs.to_dict())
        assert f"Workers ({worker})" in opening
        assert f"own race {race}" in opening

        train_target = {"terran": "scv", "protoss": "probe", "zerg": "drone"}[race]
        obs, feedback, done, info = drive(env, [
            {"name": "train", "arguments": {"target": train_target, "count": 1}},
            {"name": "advance", "arguments": {"seconds": 8}},
        ])
        later = render_observation_text(obs.to_dict(), previous={"game": {"game_time_seconds": 0}})
        assert f"Workers ({worker})" in later
        assert f"own race {race}" in later
        assert all("event_id" in event and "game_time_seconds" in event for event in feedback.events)
        assert feedback.events
        assert any(event.get("type") == "demand_accepted" for event in feedback.events)

        obs, feedback, done, info = drive(env, [
            {"name": "advance", "arguments": {"seconds": 40}},
        ])
        assert done or obs.game.game_time_seconds >= 40
        combat = dict(obs.combat or {})
        unexplained = [
            name for name, row in combat.items()
            if name != "group_0" and row.get("status") == "active" and not row.get("end_reason")
        ]
        assert unexplained == [], unexplained
    finally:
        env.close()

    episode = read_episode(env.record_path)
    assert episode["metrics"]["summary"]["integrity"]["ok"] is True
    assert episode["metrics"]["summary"]["integrity"]["errors"] == []
