"""Scan / Orbital FakeBackend coverage under the corrected contract."""
from __future__ import annotations


from tests.helpers.drive import drive
from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.config import EpisodeConfig


def _wait(seconds: float = 10.0) -> dict:
    return {"name": "advance", "arguments": {'seconds': seconds}}


def test_scan_waits_without_orbital_then_completes() -> None:
    backend = FakeBackend(mineral_income_per_second=40.0)
    env = Environment(backend)
    obs = env.reset(EpisodeConfig(game_time_limit_seconds=400.0))
    assert obs.scan_ready == 0
    cc_id = obs.structures[0]["id"]

    drive(env, [{"name": "scan", "arguments": {'target': "zone_3"}}, _wait(5)])
    obs, _, _, _ = drive(env, [_wait(5)])
    assert any(
        d.action == "scan" and d.is_active for d in env.task_manager.active_demands()
    )

    drive(env, [{"name": "build", "arguments": {'target': "supply_depot"}}, _wait(15)])
    for _ in range(12):
        obs, _, _, _ = drive(env, [_wait(10)])
        if obs.buildings.get("supply_depot", 0) >= 1:
            break
    drive(env, [{"name": "build", "arguments": {'target': "barracks"}}, _wait(20)])
    for _ in range(20):
        obs, _, _, _ = drive(env, [_wait(10)])
        if obs.buildings.get("barracks", 0) >= 1:
            break
    drive(env,
        [{"name": "morph_townhall", "arguments": {'target': cc_id, 'to': "orbital_command"}}, _wait(20)]
    )
    for _ in range(25):
        obs, _, _, _ = drive(env, [_wait(10)])
        if obs.orbital_count >= 1 and not any(
            d.action == "scan" and d.is_active for d in env.task_manager.demands.values()
        ):
            break
    assert obs.orbital_count >= 1
    assert not any(d.action == "scan" and d.is_active for d in env.task_manager.demands.values())
    env.close()


def test_scan_invalid_zone_fails() -> None:
    backend = FakeBackend()
    env = Environment(backend)
    env.reset(EpisodeConfig())
    backend.buildings = {"orbital_command": 1, "command_center": 0}
    backend.structure_types = {"townhall_0": "orbital_command"}
    backend.orbital_energy = 100.0
    drive(env, [{"name": "scan", "arguments": {'target': "zone_99"}}, _wait(5)])
    for _ in range(5):
        obs, _, _, _ = drive(env, [_wait(5)])
        failed = [
            d
            for d in env.task_manager.demands.values()
            if d.action == "scan" and d.state.value == "failed"
        ]
        if failed:
            break
    assert failed
    assert "invalid_zone" in (failed[0].failure_reason or "")
    env.close()
