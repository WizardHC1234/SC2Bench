"""Public army tools stay separate while reusing internal combat tasks."""
from sc2bench_env import Environment, ToolCall
from sc2bench_env.backends.fake import FakeBackend


def _decision(env, calls, *, seconds=1):
    turn = env.begin_tool_turn()
    results = [turn.call(ToolCall(name, arguments)) for name, arguments in calls]
    assert turn.call(ToolCall("advance", {"seconds": seconds})).status == "decision_ready"
    observation, feedback, _terminated, _info = env.step(turn.finish())
    return observation, feedback, results


def test_public_schema_replaces_overloaded_combat_tool():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        actions = {spec.name for spec in env.tool_specs() if spec.kind == "action"}
        assert {"dispatch_group", "attack_group", "defend_group", "reinforce_group",
                "merge_groups", "retreat_group"} <= actions
        assert "combat" not in actions and "retreat" not in actions
    finally:
        env.close()


def test_split_transfer_merge_and_return_use_stable_group_ids():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        env.backend.units["marine"] = 12
        observation, _, results = _decision(env, [
            ("dispatch_group", {
                "source": "group_0", "target": "zone_3", "units": {"marine": 6},
                "order": "attack",
            }),
        ])
        assert results[0].data["effect"]["group"] == "group_1"
        assert observation.combat["group_1"]["alive"] == {"marine": 6}

        observation, _, _ = _decision(env, [
            ("dispatch_group", {
                "source": "group_1", "target": "zone_2", "units": {"marine": 2},
                "order": "defend",
            }),
        ])
        assert observation.combat["group_1"]["alive"] == {"marine": 4}
        assert observation.combat["group_2"]["style"] == "defend"

        observation, feedback, results = _decision(env, [
            ("reinforce_group", {
                "source": "group_1", "destination": "group_2",
                "units": {"marine": 1},
            }),
        ])
        assert results[0].data["execution"] == "transfer_pending"
        assert observation.combat["group_1"]["alive"] == {"marine": 3}
        assert observation.combat["group_2"]["alive"] == {"marine": 3}
        assert any(event.get("type") == "group_transfer_completed"
                   for event in feedback.events)

        observation, _, _ = _decision(env, [
            ("merge_groups", {"destination": "group_1", "sources": ["group_2"]}),
        ])
        assert "group_2" not in observation.combat
        assert observation.combat["group_1"]["alive"] == {"marine": 6}

        observation, _, _ = _decision(env, [
            ("retreat_group", {"group": "group_1"}),
        ], seconds=5)
        assert set(observation.combat) == {"group_0"}
        assert observation.combat["group_0"]["alive"] == {"marine": 12}
    finally:
        env.close()
