"""Actual worker/producer loss recovery; no resource or fast-build cheats.

Only the isolated test fixture kills entities to reproduce losses. All build
and train requests go through public JSON; Sharpy alone resumes construction.
These are fault-path checks, not natural-match or win-rate evidence.
"""

import json
import os

import pytest

from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.obs_invariants import assert_obs_consistent

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def wait(seconds=2):
    return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}


def advance_until(env, predicate, max_seconds=150):
    start = env.backend.snapshot().game_time_seconds
    while env.backend.snapshot().game_time_seconds - start < max_seconds:
        obs, _, done, _ = env.step([wait()])
        assert_obs_consistent(obs)
        assert not done, obs.to_dict()
        if predicate(obs):
            return obs
    raise AssertionError(f"condition not reached after {max_seconds} game seconds")


def install_loss_fixture(monkeypatch, scenario):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2bench_env.backends.sharpy.races import terran
    from sharpy.plans import BuildOrder
    from sharpy.plans.acts import ActBase

    state = {"armed": False, "killed": False, "builder": None,
             "building": None, "replacement": None, "lost_producers": [],
             "workers_before_loss": None}

    class LossFixture(ActBase):
        async def execute(self):
            if scenario == "worker":
                depots = self.ai.structures(UnitTypeId.SUPPLYDEPOT).not_ready
                builders = self.ai.workers.filter(lambda worker: worker.is_constructing_scv)
                if state["armed"] and not state["killed"] and depots and builders:
                    depot = depots.first
                    builder = builders.closest_to(depot)
                    if builder.distance_to(depot) <= depot.radius + 1:
                        state["builder"], state["building"] = builder.tag, depot.tag
                        state["workers_before_loss"] = self.ai.workers.amount
                        await self.ai.client.debug_kill_unit([builder.tag])
                        state["killed"] = True
                elif state["killed"] and depots and builders:
                    depot = depots.find_by_tag(state["building"])
                    if depot is not None:
                        replacement = builders.closest_to(depot)
                        if (replacement.tag != state["builder"]
                                and replacement.distance_to(depot) <= depot.radius + 1):
                            state["replacement"] = replacement.tag
            elif scenario == "producer" and state["armed"] and not state["killed"]:
                barracks = self.ai.structures(UnitTypeId.BARRACKS).ready
                marines = self.ai.units(UnitTypeId.MARINE)
                if marines.amount == 1 and barracks and any(building.orders for building in barracks):
                    tags = [building.tag for building in barracks]
                    tags += [building.tag for building in self.ai.structures(UnitTypeId.REFINERY)]
                    state["lost_producers"] = tags
                    await self.ai.client.debug_kill_unit(tags)
                    state["killed"] = True
            elif scenario in {"addon", "technology"} and state["armed"] and not state["killed"]:
                unit_type = UnitTypeId.MARAUDER if scenario == "addon" else UnitTypeId.GHOST
                lost_type = UnitTypeId.BARRACKSTECHLAB if scenario == "addon" else UnitTypeId.GHOSTACADEMY
                barracks = self.ai.structures(UnitTypeId.BARRACKS).ready
                lost = self.ai.structures(lost_type).ready
                if self.ai.units(unit_type).amount == 1 and lost and any(b.orders for b in barracks):
                    state["building"] = barracks.first.tag
                    state["lost_producers"] = [b.tag for b in lost]
                    await self.ai.client.debug_kill_unit(state["lost_producers"])
                    state["killed"] = True
            return True

    original = terran.TerranAdapter.create_tactics
    monkeypatch.setattr(terran.TerranAdapter, "create_tactics",
                        lambda adapter: BuildOrder([LossFixture(), original(adapter)]))
    return state


def check_artifacts(env):
    directory = env.record_path
    env.close()
    assert (directory / "replay.SC2Replay").stat().st_size > 0
    from sc2bench_env.recording.reader import read_episode
    summary = read_episode(directory)["summary"]
    assert summary["status"] == "interrupted" and summary["rejected_count"] == 0


def test_e2e_dead_builder_is_replaced_without_a_second_build_command(monkeypatch, tmp_path):
    state = install_loss_fixture(monkeypatch, "worker")
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(opponent="builtin_veryeasy", game_time_limit_seconds=600))
        state["armed"] = True
        env.step([{"action": "build", "target": "supply_depot"}, wait()])
        obs = advance_until(env, lambda obs: state["killed"] and
                            obs.building.get("supply_depot", {}).get("completed", 0) == 1)
        assert state["replacement"] is not None and state["replacement"] != state["builder"]
        assert obs.own_forces.workers["scv"] == state["workers_before_loss"] - 1
        assert state["builder"] not in env.backend.snapshot().info["ready_unit_tags"]["scv"]
        assert not any(d.action == "build" and d.target == "supply_depot"
                       for d in env.task_manager.active_demands())
        assert env.backend._bridge.seen_entity_tags.issuperset({state["building"]})
        orders = [d for d in env.task_manager.demands.values()
                  if d.action == "build" and d.target == "supply_depot"]
        assert len(orders) == 1 and orders[0].produced == 1
        check_artifacts(env)
    finally:
        env.close()


def test_e2e_destroyed_producer_waits_then_resumes_exact_remaining_output(monkeypatch, tmp_path):
    state = install_loss_fixture(monkeypatch, "producer")
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(opponent="builtin_veryeasy", game_time_limit_seconds=600))
        for target in ("supply_depot", "barracks", "refinery"):
            env.step([{"action": "build", "target": target}, wait()])
            advance_until(env, lambda obs: obs.building.get(target, {}).get("completed", 0) == 1)
        # The public Observation is authoritative for gas-site assertions below.
        public_before, _, _, _ = env.step([wait()])
        home = next(row for row in public_before.map_control.base_resources
                    if row["owned_gas_structure_count"] == 1)
        state["armed"] = True
        env.step([{"action": "train", "target": "marine", "count": 3}, wait()])
        obs = advance_until(env, lambda obs: state["killed"] and
                            obs.building.get("barracks", {}).get("completed", 0) == 0 and
                            obs.training.get("marine", {}).get("in_production", 0) == 0)
        assert obs.own_forces.army.get("marine", 0) == 1
        assert obs.training["marine"]["waiting_to_produce"] == 2
        assert obs.training["marine"]["order_progress"] == "1/3"
        # Allow a frame for execution-side prerequisite loss to reach the snapshot.
        obs, _, _, _ = env.step([wait(4)])
        assert obs.training["marine"].get("waiting_for") == "prerequisite:barracks"
        assert obs.building.get("barracks", {}).get("waiting_to_start", 0) == 0
        site = next(row for row in obs.map_control.base_resources if row["zone_id"] == home["zone_id"])
        assert site["owned_gas_structure_count"] == 0
        assert site["available_geyser_slots"] == home["available_geyser_slots"] + 1
        env.step([{"action": "build", "target": "barracks"}, wait()])
        obs = advance_until(env, lambda obs: obs.own_forces.army.get("marine", 0) == 3,
                            max_seconds=200)
        assert obs.training.get("marine", {}).get("waiting_to_produce", 0) == 0
        orders = [d for d in env.task_manager.demands.values() if d.action == "train" and d.target == "marine"]
        assert len(orders) == 1 and orders[0].produced == orders[0].count == 3
        obs, _, _, _ = env.step([wait(30)])
        assert obs.own_forces.army.get("marine", 0) == 3
        assert obs.building.get("refinery", {}).get("completed", 0) == 0
        check_artifacts(env)
    finally:
        env.close()


@pytest.mark.parametrize("scenario,target,missing,count", [
    ("addon", "marauder", "barracks_techlab", 3),
    ("technology", "ghost", "ghost_academy", 3),
])
def test_e2e_lost_tech_waits_for_explicit_rebuild_without_duplicate_units(
        monkeypatch, tmp_path, scenario, target, missing, count):
    state = install_loss_fixture(monkeypatch, scenario)
    env = Environment("sharpy", record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(opponent="builtin_veryeasy", game_time_limit_seconds=900))
        buildings = ["supply_depot", "barracks", "refinery", "barracks_techlab"]
        if scenario == "technology":
            buildings.append("ghost_academy")
        for building in buildings:
            env.step([{"action": "build", "target": building}, wait()])
            advance_until(env, lambda obs: obs.building.get(building, {}).get("completed", 0) == 1,
                          max_seconds=200)
        state["armed"] = True
        env.step([{"action": "train", "target": target, "count": count}, wait()])
        # Loss does not necessarily cancel an already-started unit: let the
        # engine settle that queue before checking the remaining demand.
        obs = advance_until(env, lambda obs: state["killed"] and
                            obs.training.get(target, {}).get("in_production", 0) == 0 and
                            obs.training.get(target, {}).get("waiting_for") == f"prerequisite:{missing}",
                            max_seconds=250)
        produced = obs.own_forces.army.get(target, 0)
        assert 1 <= produced < count
        if scenario == "technology":
            assert produced == 2  # paid Ghost survives the Academy's destruction
        assert obs.training[target]["order_progress"] == f"{produced}/{count}"
        assert obs.training[target]["waiting_to_produce"] == count - produced
        assert obs.building.get(missing, {}).get("completed", 0) == 0
        assert obs.building.get(missing, {}).get("waiting_to_start", 0) == 0
        assert obs.building.get("barracks", {}).get("completed", 0) == 1
        obs, _, done, _ = env.step([wait(10)])
        assert not done and obs.own_forces.army.get(target, 0) == produced
        assert obs.building.get(missing, {}).get("under_construction", 0) == 0
        env.step([{"action": "build", "target": missing}, wait()])
        obs = advance_until(env, lambda obs: obs.own_forces.army.get(target, 0) == count,
                            max_seconds=250)
        demands = [d for d in env.task_manager.demands.values()
                   if d.action == "train" and d.target == target]
        assert len(demands) == 1 and demands[0].produced == demands[0].count == count
        obs, _, done, _ = env.step([wait(30)])
        assert not done and obs.own_forces.army.get(target, 0) == count
        assert obs.training.get(target, {}).get("waiting_to_produce", 0) == 0
        check_artifacts(env)
    finally:
        env.close()
