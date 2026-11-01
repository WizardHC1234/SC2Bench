"""Real client boot for each supported own race.

Debug resources only keep the match from stalling. They do not train, build,
or cast. Enable with SC2BENCH_E2E=1.
"""
from __future__ import annotations


from tests.helpers.drive import drive
import os

import pytest

from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def _wait(seconds: float = 8.0) -> dict:
    return {"name": "advance", "arguments": {"seconds": seconds}}


def _install_debug_resources(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths

    _ensure_runtime_paths()
    from sharpy.plans import BuildOrder
    from sharpy.plans.acts import ActBase
    import sc2bench_env.backends.sharpy.races as races

    class DebugResources(ActBase):
        done = False

        async def execute(self):
            if not self.done or self.ai.minerals < 1000 or self.ai.vespene < 1000:
                await self.ai.client.debug_all_resources()
                await self.ai.client.debug_fast_build()
                self.done = True
            return True

    original = races.get_adapter

    def wrapped(race):
        adapter = original(race)
        create = adapter.create_tactics

        def create_tactics():
            return BuildOrder([DebugResources(), create()])

        adapter.create_tactics = create_tactics
        return adapter

    monkeypatch.setattr(races, "get_adapter", wrapped)


@pytest.mark.parametrize("race", ["terran", "protoss", "zerg"])
def test_e2e_race_boots_and_does_not_auto_supply(race, monkeypatch) -> None:
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    _install_debug_resources(monkeypatch)
    env = Environment("sharpy")
    try:
        obs = env.reset(
            EpisodeConfig(
                race=race,
                enemy_race="terran",
                opponent="veryeasy",
                game_time_limit_seconds=180.0,
            )
        )
        assert_obs_consistent(obs)
        assert obs.race == race
        assert obs.game.race == race
        ceiling = 14 if race == "zerg" else 15
        opening_supply = int(obs.resources.supply_cap)
        assert 0 < opening_supply <= ceiling
        for _ in range(3):
            obs, _, terminated, _ = drive(env, [_wait()])
            assert_obs_consistent(obs)
            assert not terminated, obs.game
            assert obs.race == race
            # No supply order was submitted, so the cap must stay at the opening.
            assert int(obs.resources.supply_cap) <= ceiling
    finally:
        env.close()
