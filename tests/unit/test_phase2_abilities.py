"""Phase 2 FakeBackend coverage: upgrade, call_mule, scout replace, structures."""
from __future__ import annotations


from tests.helpers.drive import drive, submit_calls
from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.runtime.task_manager import TaskManager

from tests.helpers.obs_invariants import assert_obs_consistent


def _wait(seconds: float = 10.0) -> dict:
    return {"name": "advance", "arguments": {'seconds': seconds}}


def test_structures_expose_stable_townhall_id() -> None:
    env = Environment(FakeBackend())
    obs = env.reset(EpisodeConfig())
    assert_obs_consistent(obs)
    assert obs.structures == [{"id": "townhall_0", "type": "command_center"}]
    env.close()


def test_upgrade_command_center_to_orbital() -> None:
    backend = FakeBackend(mineral_income_per_second=40.0)
    env = Environment(backend)
    env.reset(EpisodeConfig(game_time_limit_seconds=400.0))

    drive(env, [{"name": "build", "arguments": {'target': "supply_depot"}}, _wait(20)])
    for _ in range(12):
        obs, _, _, _ = drive(env, [_wait(10)])
        if obs.buildings.get("supply_depot", 0) >= 1:
            break
    drive(env, [{"name": "build", "arguments": {'target': "barracks"}}, _wait(20)])
    for _ in range(15):
        obs, _, _, _ = drive(env, [_wait(10)])
        if obs.buildings.get("barracks", 0) >= 1:
            break

    obs, feedback, _, _ = drive(env,
        [{"name": "morph_townhall", "arguments": {'target': "townhall_0", 'to': "orbital_command"}}, _wait(5)]
    )
    assert feedback.receipts[0].result == "accepted"
    for _ in range(20):
        obs, _, _, _ = drive(env, [_wait(5)])
        assert_obs_consistent(obs)
        if obs.orbital_count >= 1:
            break
    assert obs.orbital_count >= 1
    assert any(row["id"] == "townhall_0" and row["type"] == "orbital_command" for row in obs.structures)
    assert obs.scan_ready >= 1 or obs.mule_ready >= 1
    env.close()


def test_call_mule_and_scan_one_shot() -> None:
    backend = FakeBackend(mineral_income_per_second=40.0)
    backend.buildings = {"command_center": 0, "orbital_command": 1, "barracks": 1, "supply_depot": 1}
    backend.structure_types = {"townhall_0": "orbital_command"}
    backend.orbital_energy = 100.0
    env = Environment(backend)
    obs = env.reset(EpisodeConfig())
    # reset() restarts FakeBackend; re-seed after reset.
    backend.buildings = {"command_center": 0, "orbital_command": 1, "barracks": 1, "supply_depot": 1}
    backend.structure_types = {"townhall_0": "orbital_command"}
    backend.orbital_energy = 100.0
    obs = env._build_observation(backend.snapshot())
    assert obs.scan_ready >= 1

    obs, feedback, _, _ = drive(env, [{"name": "call_mule", "arguments": {}}, _wait(5)])
    assert feedback.receipts[0].result == "accepted"
    for _ in range(4):
        obs, _, _, _ = drive(env, [_wait(2)])
        if not any(d.action == "call_mule" and d.is_active for d in env.task_manager.demands.values()):
            break
    assert not any(d.action == "call_mule" and d.is_active for d in env.task_manager.demands.values())

    backend.orbital_energy = 80.0
    zone = obs.zones[0]
    obs, feedback, _, _ = drive(env, [{"name": "scan", "arguments": {'target': zone}}, _wait(5)])
    assert feedback.receipts[0].result == "accepted"
    for _ in range(4):
        obs, _, _, _ = drive(env, [_wait(2)])
        if not any(d.action == "scan" and d.is_active for d in env.task_manager.demands.values()):
            break
    assert not any(d.action == "scan" and d.is_active for d in env.task_manager.demands.values())
    env.close()


def test_scout_route_replaces_previous() -> None:
    manager = TaskManager()
    first = submit_calls(manager,
        [{"name": "scout", "arguments": {'route': ["zone_0", "zone_1"]}}, {"name": "advance", "arguments": {'seconds': 5}}],
        game_time=0.0,
    )
    second = submit_calls(manager,
        [{"name": "scout", "arguments": {'route': ["zone_2"]}}, {"name": "advance", "arguments": {'seconds': 5}}],
        game_time=1.0,
    )
    assert first[0].result == "accepted"
    assert second[0].result == "accepted"
    active = [d for d in manager.active_demands() if d.action == "scout"]
    assert len(active) == 1
    assert list(active[0].route or ()) == ["zone_2"]
    cancelled = [
        d
        for d in manager.demands.values()
        if d.action == "scout" and d.state.value == "cancelled"
    ]
    assert len(cancelled) == 1
