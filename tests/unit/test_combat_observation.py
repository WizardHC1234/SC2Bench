"""Public activity is observed evidence, not tactical success or intent."""
from types import SimpleNamespace

import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
from sc2bench_env.backends.sharpy.combat_observation import observe_group_activity

_ensure_runtime_paths()
from sc2.position import Point2
from sc2bench_env.backends.sharpy.bot import BenchBot


def unit(tag, x=0, y=0, **kwargs):
    result = SimpleNamespace(tag=tag, position=Point2((x, y)), is_visible=True,
                             weapon_cooldown=0, is_snapshot=False, is_memory=False,
                             is_hallucination=False)
    result.distance_to = lambda point: result.position.distance_to(point)
    result.__dict__.update(kwargs)
    return result


def test_no_visible_contact_is_zero_not_fighting():
    assert observe_group_activity([unit(1)], [], []) == {
        "visible_enemy_nearby": {"units": 0, "buildings": 0},
        "weapon_cooldown_active_count": 0,
    }


@pytest.mark.parametrize("flags", [dict(is_visible=False), dict(is_snapshot=True),
                                  dict(is_memory=True), dict(is_hallucination=True)])
def test_fog_and_non_genuine_enemies_do_not_count(flags):
    assert observe_group_activity([unit(1)], [unit(2, **flags)], [])[
        "visible_enemy_nearby"] == {"units": 0, "buildings": 0}


def test_radius_is_per_member_not_centroid_and_enemy_not_double_counted():
    facts = observe_group_activity([unit(1, 0), unit(2, 80), unit(3, 1)],
                                   [unit(4, 15), unit(5, 40), unit(6, 95.01)],
                                   [unit(7, 80, is_ready=False)])
    assert facts["visible_enemy_nearby"] == {"units": 1, "buildings": 1}


def test_weapon_cooldown_is_independent_of_visible_enemies():
    facts = observe_group_activity([unit(1, weapon_cooldown=3), unit(2)], [], [])
    assert facts["weapon_cooldown_active_count"] == 1
    assert facts["visible_enemy_nearby"]["units"] == 0


def test_missing_members_or_cooldown_stays_unknown():
    assert observe_group_activity([], [unit(2)], []) == {
        "visible_enemy_nearby": None, "weapon_cooldown_active_count": None,
    }
    assert observe_group_activity([unit(1, weapon_cooldown=None)], [], [])[
        "weapon_cooldown_active_count"] is None


def test_bot_reports_execution_without_changing_internal_phase_or_orders():
    act = SimpleNamespace(_tags=[1], _bound=True, phase="fight", alive_counts={"marine": 1})
    bot = SimpleNamespace(_macro_tasks=[{"action": "combat", "task_id": "task",
                                        "_act": act, "target": "zone_15"}],
                          units=[unit(1), unit(2, weapon_cooldown=5)],
                          enemy_units=[], enemy_structures=[], zone_registry=None)
    row = BenchBot._collect_combat_progress(bot)["task"]
    assert row["phase"] == "executing" and act.phase == "fight"
    assert row["target"] == "zone_15"
    assert row["visible_enemy_nearby"] == {"units": 0, "buildings": 0}
    assert row["weapon_cooldown_active_count"] == 0  # Other groups cannot leak.


def test_fake_public_context_keeps_observations_unknown_and_rules_explicit():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        env.backend.units["marine"] = 2
        obs, _, _, _ = env.step([
            {"action": "combat", "style": "attack", "target": "zone_15", "units": {"marine": 2}},
            {"action": "wait", "any_of": [{"condition": "interval", "seconds": 1}]},
        ])
        row = obs.combat["group_1"]
        assert row["phase"] == "executing"
        assert row["visible_enemy_nearby"] is None
        assert row["weapon_cooldown_active_count"] is None
        text = "\n".join(obs.section_lines())
        assert "phase executing" in text
        assert "Visible enemies within 15 of on-map members: unknown" in text
        prompt = env.get_system_prompt()
        assert "does not automatically become a whole-map search" in prompt
        assert "Same style/target is a no-op" in prompt
        assert "not proof of ongoing fighting" in prompt
    finally:
        env.close()
