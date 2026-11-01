"""Real SC2 check for reinforcement, group merge and return to group_0."""
from __future__ import annotations

import os

import pytest

from tests.e2e.test_combat_e2e import _build_ready, _wait
from tests.helpers.drive import drive
from tests.helpers.obs_invariants import assert_obs_consistent


pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def test_reinforce_merge_and_return_preserve_real_unit_membership(tmp_path):
    from sc2bench_env import Environment
    from sc2bench_env.interface.config import EpisodeConfig

    env = Environment("sharpy", record_dir=tmp_path)

    def until(predicate, *, steps=90, seconds=2):
        for _ in range(steps):
            observation, _, ended, _ = drive(env, [_wait(seconds)])
            assert not ended, "episode ended before the group lifecycle checkpoint"
            assert_obs_consistent(observation)
            if predicate(observation):
                return observation
        pytest.fail("group lifecycle checkpoint did not occur within bounded waits")

    try:
        env.reset(EpisodeConfig(
            blocking_decisions=True,
            opponent="veryeasy",
            game_time_limit_seconds=900,
        ))
        _build_ready(env, "supply_depot", max_steps=35)
        _build_ready(env, "barracks", max_steps=35)
        drive(env, [
            {"name": "build", "arguments": {"target": "supply_depot"}},
            {"name": "train", "arguments": {"target": "marine", "count": 6}},
            _wait(8),
        ])
        observation = until(
            lambda obs: obs.own_forces.free.get("marine", 0) == 6,
            seconds=4,
        )
        own_main = next(
            zone["zone_id"] for zone in observation.zone_state
            if zone["zone_role"] == "own_main"
        )
        own_natural = next(
            zone["zone_id"] for zone in observation.zone_state
            if zone["zone_role"] == "own_natural"
        )

        observation, feedback, ended, _ = drive(env, [
            {"name": "combat", "arguments": {
                "style": "defend", "target": own_main,
                "units": {"marine": 2},
            }},
            {"name": "combat", "arguments": {
                "style": "defend", "target": own_natural,
                "units": {"marine": 2},
            }},
            _wait(1),
        ])
        assert not ended
        assert [receipt.result for receipt in feedback.receipts[:2]] == ["accepted", "accepted"]
        observation = until(
            lambda obs: set(obs.combat) >= {"group_0", "group_1", "group_2"}
            and len(obs.combat["group_1"].get("member_tags", [])) == 2
            and len(obs.combat["group_2"].get("member_tags", [])) == 2,
        )
        original_tags = (
            set(observation.combat["group_1"]["member_tags"])
            | set(observation.combat["group_2"]["member_tags"])
        )
        assert len(original_tags) == 4

        observation, feedback, ended, _ = drive(env, [
            {"name": "combat", "arguments": {
                "group": "group_1", "units": {"marine": 1},
            }},
            _wait(1),
        ])
        assert not ended and feedback.receipts[0].result == "accepted"
        observation = until(
            lambda obs: len(obs.combat.get("group_1", {}).get("member_tags", [])) == 3,
        )
        reinforced_tags = set(observation.combat["group_1"]["member_tags"])
        source_tags = set(observation.combat["group_2"]["member_tags"])
        assert len(reinforced_tags) == 3
        assert len(source_tags) == 2
        assert not reinforced_tags & source_tags

        observation, feedback, ended, _ = drive(env, [
            {"name": "merge_groups", "arguments": {
                "destination": "group_1", "sources": ["group_2"],
            }},
            _wait(1),
        ])
        assert not ended and feedback.receipts[0].result == "accepted"
        observation = until(
            lambda obs: "group_2" not in obs.combat
            and len(obs.combat.get("group_1", {}).get("member_tags", [])) == 5,
        )
        merged_tags = set(observation.combat["group_1"]["member_tags"])
        assert merged_tags == reinforced_tags | source_tags
        assert observation.combat["group_1"]["alive"] == {"marine": 5}
        assert observation.own_forces.free == {"marine": 1}
        assert any(
            event.get("type") == "groups_merged"
            and event.get("sources") == ["group_2"]
            and event.get("destination") == "group_1"
            and event.get("members") == {"group_2": {"marine": 2}}
            for event in observation.recent_events
        )

        observation, feedback, ended, _ = drive(env, [
            {"name": "retreat", "arguments": {"group": "group_1"}},
            _wait(1),
        ])
        assert not ended and feedback.receipts[0].result == "accepted"
        observation = until(lambda obs: "group_1" not in obs.combat)
        assert observation.own_forces.free == {"marine": 6}
        assert observation.combat["group_0"]["alive"] == {"marine": 6}
        assert not observation.own_forces.assigned
        assert_obs_consistent(observation)
    finally:
        env.close()
