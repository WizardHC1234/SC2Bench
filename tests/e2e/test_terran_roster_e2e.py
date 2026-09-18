"""Cheat-assisted production acceptance, not an economic/battle benchmark.

Debug grants resources and accelerates builds only in this isolated test.
Every unit still has to be trained through the platform's real command path.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("SC2BENCH_E2E") != "1", reason="Set SC2BENCH_E2E=1")


def test_e2e_complete_terran_production_roster(monkeypatch):
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig
    from sc2bench_env.interface.action_catalog import targets_for_action
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2bench_env.backends.sharpy.races import terran
    from sharpy.plans import BuildOrder
    from sharpy.plans.acts import ActBase

    data_rows = {}

    class DebugSetup(ActBase):
        done = False

        async def execute(self):
            if self.done and (self.ai.minerals < 1000 or self.ai.vespene < 1000):
                await self.ai.client.debug_all_resources()
            if not self.done:
                await self.ai.client.debug_all_resources()
                await self.ai.client.debug_fast_build()
                data_rows["upgrades"] = [(key, value.name, value.cost.minerals, value.cost.vespene,
                                          value._proto.ability_id, value.cost.time)
                                         for key, value in self.ai._game_data.upgrades.items()
                                         if key in {21, 76, 139, 140, 289, 300}]
                print("LIVE_SC2_TECH_IDS", data_rows["upgrades"], flush=True)
                data_rows["units"] = {
                    name: (self.ai._game_data.units[pair[0].value].cost.minerals,
                           self.ai._game_data.units[pair[0].value].cost.vespene)
                    for name, pair in terran.UNITS.items()
                }
                data_rows["research"] = {
                    name: (self.ai._game_data.upgrades[76 if name == "yamato_cannon" else upgrade.value].cost.minerals,
                           self.ai._game_data.upgrades[76 if name == "yamato_cannon" else upgrade.value].cost.vespene)
                    for name, upgrade in terran.RESEARCH.items()
                }
                print("LIVE_SC2_RESEARCH_ABILITIES", [
                    (name, self.ai._game_data.upgrades[upgrade.value].research_ability.id,
                     self.ai._game_data.upgrades[upgrade.value].research_ability.exact_id)
                    for name, upgrade in terran.RESEARCH.items() if name != "yamato_cannon"
                ], flush=True)
                self.done = True
            return True

    class TestAdapter(terran.TerranAdapter):
        def create_tactics(self):
            return BuildOrder([DebugSetup(), super().create_tactics()])

    monkeypatch.setattr(terran, "get_adapter", lambda race: TestAdapter())
    env = Environment("sharpy")
    wait = {"action": "wait", "any_of": [{"condition": "interval", "seconds": 8}]}
    try:
        env.reset(EpisodeConfig(opponent="builtin_veryeasy", game_time_limit_seconds=1200))
        for target in ("supply_depot", "barracks", "factory", "starport", "armory",
                       "ghost_academy", "fusion_core", "engineering_bay", "bunker", "missile_turret", "sensor_tower",
                       "barracks_techlab", "factory_techlab", "starport_techlab"):
            obs, feedback, ended, _ = env.step([{"action": "build", "target": target}, wait])
            assert feedback.receipts[0].result == "accepted"
            for _ in range(20):
                if obs.buildings.get(target, 0):
                    break
                obs, _, ended, _ = env.step([wait])
            assert not ended and obs.buildings.get(target, 0), target
        for _ in range(6):
            env.step([{"action": "build", "target": "supply_depot"}, wait])
        specs = targets_for_action("train")
        for spec in specs:
            obs, feedback, ended, _ = env.step([{"action": "train", "target": spec.name, "count": 1}, wait])
            assert feedback.receipts[0].result == "accepted", spec.name
            for _ in range(12):
                produced = sum(d.produced for d in env.task_manager.demands.values()
                               if d.action == "train" and d.target == spec.name)
                if produced >= 1:
                    break
                obs, _, ended, _ = env.step([wait])
            assert not ended and produced >= 1, spec.name
        for spec in specs:
            assert data_rows["units"][spec.name] == (spec.minerals, spec.vespene), spec.name
        for spec in targets_for_action("research"):
            assert data_rows["research"][spec.name] == (spec.minerals, spec.vespene), spec.name
            obs, feedback, ended, _ = env.step([{"action": "research", "target": spec.name}, wait])
            assert feedback.receipts[0].result == "accepted", spec.name
            for _ in range(12):
                if obs.research.get(spec.name) == "completed":
                    break
                obs, _, ended, _ = env.step([wait])
            assert not ended and obs.research.get(spec.name) == "completed", spec.name
        requested = {name: 1 for name, count in obs.own_forces.free.items() if count and name in terran.UNITS and name != "scv"}
        assert {"ghost", "cyclone", "raven", "liberator", "viking"} <= set(requested)
        obs, feedback, ended, _ = env.step([{"action": "combat", "style": "defend", "target": "zone_0", "units": requested}, wait])
        assert feedback.receipts[0].result == "accepted"
        assert not ended and obs.combat
    finally:
        env.close()
