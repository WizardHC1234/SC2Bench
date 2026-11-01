"""FakeBackend + parser coverage for combat binding."""
from __future__ import annotations


from tests.helpers.drive import drive
import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.actions import ActionValidationError, parse_decision
from sc2bench_env.interface.config import EpisodeConfig


def _wait(seconds: float = 1.0) -> dict:
    return {"name": "advance", "arguments": {'seconds': seconds}}


def test_parse_combat_action() -> None:
    batch = parse_decision(
        [
            {"name": "combat", "arguments": {'style': "attack", 'target': "zone_6", 'units': {"marine": 2, "marauder": 1}}},
            _wait(),
        ]
    )
    action = batch.actions[0]
    assert action.action == "combat"
    assert action.style == "attack"
    assert action.target == "zone_6"
    assert action.units == {"marine": 2, "marauder": 1}


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "combat", "arguments": {'style': "blink", 'target': "zone_1", 'units': {"marine": 1}}},
        {"name": "combat", "arguments": {'style': "attack", 'target': "base", 'units': {"marine": 1}}},
        {"name": "combat", "arguments": {'style': "attack", 'target': "zone_1", 'units': {}}},
        {"name": "combat", "arguments": {'style': "attack", 'target': "zone_1", 'units': {"scv": 1}}},
        {"name": "combat", "arguments": {'style': "attack", 'target': "zone_1", 'units': {"marine": 0}}},
    ],
)
def test_parse_combat_rejects_bad_payload(payload: dict) -> None:
    with pytest.raises(ActionValidationError):
        parse_decision([payload, _wait()])


def test_fake_combat_binds_and_rejects_insufficient() -> None:
    env = Environment(FakeBackend())
    env.reset(EpisodeConfig(game_time_limit_seconds=60.0))
    backend = env.backend
    assert isinstance(backend, FakeBackend)
    backend.units["marine"] = 3
    backend.supply_used = 15

    obs, feedback, _, _ = drive(env,
        [
            {"name": "combat", "arguments": {'style': "attack", 'target': "zone_3", 'units': {"marine": 2}}},
            _wait(),
        ]
    )
    assert feedback.receipts[0].result == "accepted"
    assert obs.combat["group_1"]["style"] == "attack"
    row = next(iter(obs.combat.values()))
    assert row["alive"]["marine"] == 2
    assert row["status"] == "active"

    _, feedback2, _, _ = drive(env,
        [
            {"name": "combat", "arguments": {'style': "attack", 'target': "zone_4", 'units': {"marine": 2}}},
            _wait(),
        ]
    )
    receipt = feedback2.receipts[0]
    assert receipt.result == "rejected"
    assert receipt.reason == "insufficient_units"
    assert receipt.details is not None
    assert receipt.details["missing"]["marine"] == 1
    assert receipt.details["available"]["marine"] == 1


def test_fake_identical_combat_is_noop() -> None:
    env = Environment(FakeBackend())
    env.reset(EpisodeConfig(game_time_limit_seconds=60.0))
    backend = env.backend
    assert isinstance(backend, FakeBackend)
    backend.units["banshee"] = 2
    backend.supply_used = 18

    decision = [
        {"name": "combat", "arguments": {'style': "attack", 'target': "zone_6", 'units': {"banshee": 2}}},
        _wait(),
    ]
    _, feedback, _, _ = drive(env, decision)
    assert feedback.receipts[0].result == "accepted"
    _, feedback2, _, _ = drive(env, decision)
    assert feedback2.receipts[0].result == "idempotent_noop"


def test_fake_combat_ends_when_force_destroyed() -> None:
    env = Environment(FakeBackend())
    env.reset(EpisodeConfig(game_time_limit_seconds=60.0))
    backend = env.backend
    assert isinstance(backend, FakeBackend)
    backend.units["marine"] = 1
    backend.supply_used = 13

    drive(env,
        [
            {"name": "combat", "arguments": {'style': "attack", 'target': "zone_2", 'units': {"marine": 1}}},
            _wait(),
        ]
    )
    backend.units["marine"] = 0
    obs, _, _, _ = drive(env, [_wait()])
    assert set(obs.combat) == {"group_0"}
    assert any(
        event.get("type") == "combat_ended" and event.get("end_reason") == "force_destroyed"
        for event in obs.recent_events
    )


def test_fake_multiple_missions_do_not_duplicate_survivors_or_auto_replenish() -> None:
    backend = FakeBackend()
    env = Environment(backend)
    env.reset(EpisodeConfig(game_time_limit_seconds=60))
    backend.units["marine"] = 4
    drive(env, [
        {"name": "combat", "arguments": {'style': "attack", 'target': "zone_2", 'units': {"marine": 2}}},
        {"name": "combat", "arguments": {'style': "defend", 'target': "zone_0", 'units': {"marine": 2}}},
        _wait(),
    ])
    backend.units["marine"] = 3
    obs, _, _, _ = drive(env, [_wait()])
    assert obs.own_forces.assigned["marine"] == 3
    backend.units["marine"] = 5  # Simulate newly produced, free infantry.
    obs, _, _, _ = drive(env, [_wait()])
    assert obs.own_forces.assigned["marine"] == 3
    assert obs.own_forces.free["marine"] == 2
    env.close()
