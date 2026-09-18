"""Competing public scan/MULE requests with real energy and observed effects."""

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


def install_effect_observer(monkeypatch):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.effect_id import EffectId
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2bench_env.backends.sharpy.races import terran
    from sharpy.plans import BuildOrder
    from sharpy.plans.acts import ActBase

    state = {"scan_seen": False, "mule_tags": set(), "scan_zone": None}

    class Observer(ActBase):
        async def execute(self):
            center = self.ai.zone_registry.center_for(state["scan_zone"]) if state["scan_zone"] else None
            if center is not None:
                from sc2.position import Point2
                # Enemy scans or MULE landing visibility at our base must not
                # be mistaken for our requested distant Scanner Sweep.
                state["scan_seen"] |= any(
                    effect.id == EffectId.SCANNERSWEEP and effect.owner == self.ai.player_id
                    and any(position.distance_to(Point2(center)) < 20 for position in effect.positions)
                    for effect in self.ai.state.effects)
            state["mule_tags"].update(unit.tag for unit in self.ai.units(UnitTypeId.MULE))
            return True

    original = terran.TerranAdapter.create_tactics
    monkeypatch.setattr(terran.TerranAdapter, "create_tactics",
                        lambda adapter: BuildOrder([Observer(), original(adapter)]))
    return state


@pytest.mark.parametrize("first", ["scan", "call_mule"])
def test_e2e_orbital_spends_once_then_resumes_second_request(monkeypatch, tmp_path, first):
    effects = install_effect_observer(monkeypatch)
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        obs = env.reset(EpisodeConfig(opponent="builtin_veryeasy", game_time_limit_seconds=600))
        directory = env.record_path
        cc = next(row["id"] for row in obs.structures if row["type"] == "command_center")
        for target in ("supply_depot", "barracks"):
            env.step([{"action": "build", "target": target}, wait()])
            advance_until(env, lambda obs: obs.building.get(target, {}).get("completed", 0) == 1)
        env.step([{"action": "upgrade", "target": cc, "to": "orbital_command"}, wait()])
        obs = advance_until(env, lambda obs: obs.orbital_count == 1)
        energy = env.backend.snapshot().info["orbital_energies"]
        assert len(energy) == 1 and 50 <= energy[0] < 60
        scan_zone = next(row["zone_id"] for row in obs.zone_state if row["zone_role"] == "enemy_main")
        effects["scan_zone"] = scan_zone
        requests = {"scan": {"action": "scan", "target": scan_zone},
                    "call_mule": {"action": "call_mule"}}
        second = "call_mule" if first == "scan" else "scan"
        obs, feedback, done, _ = env.step([requests[first], requests[second], wait(3)])
        assert not done and all(receipt.result == "accepted" for receipt in feedback.receipts)
        demands = {d.action: d for d in env.task_manager.demands.values()
                   if d.action in requests}
        assert demands[first].produced == 1 and not demands[first].is_active
        assert demands[second].produced == 0 and demands[second].is_active
        assert demands[second].waiting_for == "energy"
        advance_until(env, lambda obs: effects["scan_seen"] if first == "scan"
                      else len(effects["mule_tags"]) == 1, max_seconds=10)
        assert demands[second].produced == 0 and demands[second].waiting_for == "energy"
        assert effects["scan_seen"] == (first == "scan"), effects
        assert len(effects["mule_tags"]) == int(first == "call_mule"), effects
        assert env.backend.snapshot().info["orbital_energies"][0] < 20
        advance_until(env, lambda obs: not any(d.is_active for d in demands.values()), max_seconds=100)
        assert all(d.produced == d.count == 1 for d in demands.values())
        # The command ends on queued cast acknowledgement, not the MULE's
        # delayed landing. Independently verify the actual world effect.
        advance_until(env, lambda obs: effects["scan_seen"] and len(effects["mule_tags"]) == 1,
                      max_seconds=10)
        _, _, done, _ = env.step([wait(30)])
        assert not done and len(effects["mule_tags"]) == 1
    finally:
        env.close()
    from sc2bench_env.recording.reader import read_episode
    summary = read_episode(directory)["summary"]
    assert summary["status"] == "interrupted" and summary["rejected_count"] == 0
    assert (directory / "replay.SC2Replay").stat().st_size > 0
