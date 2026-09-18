"""Naturally scouted enemy heat -> public scan -> observed Scanner Sweep.

No model API, synthetic heat, debug vision, energy or resource cheats.
"""

import json
import os

import pytest

from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.e2e.test_macro_losses_e2e import advance_until, wait

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def test_e2e_scan_casts_at_real_enemy_heat_area_center(monkeypatch, tmp_path):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.effect_id import EffectId
    from sharpy.managers.extensions.heat_map import HeatArea, HeatMapManager
    from sharpy.plans import BuildOrder
    from sharpy.plans.acts import ActBase
    from sc2bench_env.backends.sharpy.acts import ActScanZone
    from sc2bench_env.backends.sharpy.races import terran

    state = {"zone_id": None, "heat_seen": False, "cast_hotspot": None,
             "zone_center": None, "scan_seen": False}

    class Observer(ActBase):
        async def execute(self):
            if state["zone_id"]:
                zone = self.ai.zone_registry.resolve_zone(self.zone_manager, state["zone_id"])
                heat = self.knowledge.get_manager(HeatMapManager)
                if heat and zone:
                    state["heat_seen"] |= isinstance(heat.get_zones_hotspot([zone]), HeatArea)
            if state["cast_hotspot"] is not None:
                state["scan_seen"] |= any(
                    effect.id == EffectId.SCANNERSWEEP and effect.owner == self.ai.player_id
                    and any(position.distance_to(state["cast_hotspot"]) < 0.25
                            for position in effect.positions)
                    for effect in self.ai.state.effects)
            return True

    original_tactics = terran.TerranAdapter.create_tactics
    monkeypatch.setattr(terran.TerranAdapter, "create_tactics",
                        lambda adapter: BuildOrder([Observer(), original_tactics(adapter)]))
    original_execute = ActScanZone.execute

    async def observe_cast(act):
        selected = None
        if not act._done and act.zone_id == state["zone_id"]:
            zone = act.ai.zone_registry.resolve_zone(act.zone_manager, act.zone_id)
            heat = act.knowledge.get_manager(HeatMapManager)
            selected = heat.get_zones_hotspot([zone]) if heat and zone else None
            state["zone_center"] = zone.center_location if zone else None
        result = await original_execute(act)
        if act._done and state["cast_hotspot"] is None:
            assert isinstance(selected, HeatArea), "scan must exercise a real positive-heat hotspot"
            state["cast_hotspot"] = selected.center
        return result

    # Observe the selected target without changing the hotspot, energy or cast.
    monkeypatch.setattr(ActScanZone, "execute", observe_cast)
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        obs = env.reset(EpisodeConfig(opponent="builtin_veryeasy", map_name="KairosJunctionLE",
                                      race="terran", enemy_race="terran", game_time_limit_seconds=600))
        directory = env.record_path
        state["zone_id"] = next(z["zone_id"] for z in obs.zone_state if z["zone_role"] == "enemy_main")
        cc = next(s["id"] for s in obs.structures if s["type"] == "command_center")
        env.step([{"action": "scout", "route": [state["zone_id"]]},
                  {"action": "build", "target": "supply_depot"}, wait()])
        advance_until(env, lambda obs: obs.building.get("supply_depot", {}).get("completed", 0) == 1)
        env.step([{"action": "build", "target": "barracks"}, wait()])
        advance_until(env, lambda obs: obs.building.get("barracks", {}).get("completed", 0) == 1)
        env.step([{"action": "upgrade", "target": cc, "to": "orbital_command"}, wait()])
        advance_until(env, lambda obs: obs.orbital_count == 1 and state["heat_seen"])
        _, feedback, done, _ = env.step([{"action": "scan", "target": state["zone_id"]}, wait(3)])
        assert not done and feedback.receipts[0].result == "accepted"
        advance_until(env, lambda obs: state["scan_seen"], max_seconds=10)
        assert state["cast_hotspot"].distance_to(state["zone_center"]) > 0.25
        scans = [d for d in env.task_manager.demands.values() if d.action == "scan"]
        assert len(scans) == 1 and scans[0].produced == 1 and not scans[0].is_active
        assert not scans[0].failure_reason
    finally:
        env.close()
    from sc2bench_env.recording.reader import read_episode
    summary = read_episode(directory)["summary"]
    assert summary["status"] == "interrupted" and summary["rejected_count"] == 0
    assert (directory / "replay.SC2Replay").stat().st_size > 0
