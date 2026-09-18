"""Stable army identity, same-frame ordering and return-to-home contracts."""
import pytest
from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.actions import ActionValidationError, attach_retry_ids, parse_decision
from sc2bench_env.interface.action_catalog import decision_json_schema


def dispatch(n=6, target="zone_15"):
    return {"action": "combat", "style": "attack", "target": target, "units": {"marine": n}}


def step(env, *commands, seconds=1):
    return env.step([*commands, {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}])


@pytest.fixture
def env():
    e = Environment(FakeBackend(), record_trajectory=False)
    e.reset()
    e.backend.units["marine"] = 10
    yield e
    e.close()


def test_dispatch_names_and_pool(env):
    obs, feedback, _, _ = step(env, dispatch())
    assert feedback.receipts[0].group == "group_1"
    assert obs.combat["group_0"]["alive"] == {"marine": 4}
    assert obs.combat["group_0"]["style"] == "defend"
    assert obs.combat["group_1"]["alive"] == {"marine": 6}


def test_retarget_retains_survivors_no_new_binding(env):
    step(env, dispatch())
    env.backend.units["marine"] = 8
    step(env)
    # Fake loss attribution is deterministic, not a combat simulation.
    alive = dict(env.backend._queue[0].alive_units)
    original = next(iter(env.task_manager.demands.values()))
    obs, f, _, _ = step(env, {"action": "combat", "group": "group_1", "style": "attack", "target": "zone_10"})
    assert f.receipts[0].result == "accepted"
    assert len(env.task_manager.demands) == 1
    assert next(iter(env.task_manager.demands.values())) is original
    assert obs.combat["group_1"]["alive"] == alive
    assert obs.combat["group_1"]["requested"] == {"marine": 6}
    assert obs.combat["group_1"]["target"] == "zone_10"
    assert obs.own_forces.assigned == alive


def test_return_merges_survivors_and_never_reuses_number(env):
    step(env, dispatch())
    obs, f, _, _ = step(env, {"action": "retreat", "group": "group_1"})
    assert f.receipts[0].result == "accepted"
    assert obs.combat["group_1"]["phase"] == "withdrawing"
    assert obs.combat["group_0"]["alive"] == {"marine": 4}
    obs, _, _, _ = step(env, seconds=4)
    assert set(obs.combat) == {"group_0"}
    assert obs.combat["group_0"]["alive"] == {"marine": 10}
    assert any(e.get("group") == "group_1" and e.get("end_reason") == "withdrawn" for e in obs.recent_events)
    obs, f, _, _ = step(env, dispatch())
    assert f.receipts[0].group == "group_2"
    assert "group_1" not in obs.combat
    _, f, _, _ = step(env, {"action": "retreat", "group": "group_1"})
    assert f.receipts[0].reason == "group_not_found"


def test_parallel_order_changes_only_selected_group(env):
    step(env, dispatch(4), dispatch(3, "zone_14"))
    obs, f, _, _ = step(env, {"action": "combat", "group": "group_1", "style": "attack", "target": "zone_12"})
    assert obs.combat["group_2"]["target"] == "zone_14"
    assert obs.combat["group_2"]["alive"] == {"marine": 3}
    obs, f, _, _ = step(env, dispatch(4, "zone_11"))
    assert f.receipts[0].reason == "insufficient_units"
    assert f.receipts[0].details["available"] == {"marine": 3}


def test_same_batch_create_then_update_then_retreat(env):
    obs, f, _, _ = step(env, dispatch(),
        {"action": "combat", "group": "group_1", "style": "attack", "target": "zone_10"},
        {"action": "retreat", "group": "group_1"})
    assert all(r.result == "accepted" for r in f.receipts)
    assert obs.combat["group_1"]["phase"] == "withdrawing"
    assert obs.combat["group_1"]["target"] == "zone_10"


def test_noop_and_retry_identity(env):
    step(env, dispatch())
    order = {"action": "combat", "group": "group_1", "style": "attack", "target": "zone_10"}
    batch = parse_decision([order, {"action": "wait"}])
    assert attach_retry_ids(batch, ["retry"]).actions[0].group == "group_1"
    step(env, order)
    _, f, _, _ = step(env, order)
    assert f.receipts[0].result == "idempotent_noop"
    assert env.task_manager.group_counter == 1


@pytest.mark.parametrize("command", [
    {"action": "retreat", "group": "group_0"},
    {"action": "retreat", "group": "group_01"},
    {"action": "retreat", "group": "made_up"},
    {"action": "retreat", "group": "group_1", "target": "zone_0"},
    {"action": "combat", "group": "group_1", "units": {"marine": 1}, "style": "attack", "target": "zone_1"},
    {"action": "combat", "style": "attack", "target": "zone_1"},
])
def test_invalid_group_shape_rejects_whole_batch_without_effect(env, command):
    with pytest.raises(ActionValidationError):
        parse_decision([dispatch(), command, {"action": "wait"}])
    _, f, _, _ = step(env, dispatch(), command)
    assert not env.task_manager.demands
    assert env.task_manager.group_counter == 0


def test_schema_matches_create_update_return():
    jsonschema = pytest.importorskip("jsonschema")
    for command in [dispatch(), {"action": "combat", "group": "group_1", "style": "attack", "target": "zone_10"},
                    {"action": "retreat", "group": "group_1"}]:
        jsonschema.validate([command, {"action": "wait"}], decision_json_schema())


def test_episode_reset_restarts_group_names(env):
    step(env, dispatch())
    env.reset()
    env.backend.units["marine"] = 10
    _, f, _, _ = step(env, dispatch())
    assert f.receipts[0].group == "group_1"
