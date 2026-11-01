"""Public activity is observed evidence, not tactical success or intent."""
from tests.helpers.drive import drive
from sc2bench_env.interface.observations import observation_lines, render_brief_observation_text
from sc2bench_env.adapters.llm import LLMAdapter
from sc2bench_env.adapters.llm.tool_results import render_tool_result
from types import SimpleNamespace

import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
from sc2bench_env.backends.sharpy.combat_observation import observe_group_activity
from sc2bench_env.runtime.observation_builder import combat_summary

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
    act = SimpleNamespace(
        _tags=[1], _bound=True, phase="fight", alive_counts={"marine": 1},
        _holding_since=12.5,
    )
    bot = SimpleNamespace(_macro_tasks=[{"action": "combat", "task_id": "task",
                                        "_act": act, "target": "zone_15"}],
                          units=[unit(1), unit(2, weapon_cooldown=5)],
                          enemy_units=[], enemy_structures=[], zone_registry=None)
    row = BenchBot._collect_combat_progress(bot)["task"]
    assert row["phase"] == "executing" and act.phase == "fight"
    assert row["holding_since_game_time"] == 12.5
    assert row["target"] == "zone_15"
    assert row["visible_enemy_nearby"] == {"units": 0, "buildings": 0}
    assert row["weapon_cooldown_active_count"] == 0  # Other groups cannot leak.


def test_completed_attack_is_identified_as_automatic_defend_hold():
    demand = SimpleNamespace(
        action="combat", demand_id="combat_3", group="group_3",
        style="attack", target="zone_14", units={"roach": 5, "zergling": 10},
    )
    manager = SimpleNamespace(active_demands=lambda: [demand])
    snapshot = SimpleNamespace(
        game_time_seconds=180,
        units={"roach": 5, "zergling": 6},
        info={"combat_progress": {"combat_3": {
            "style": "defend", "alive": {"roach": 5, "zergling": 6},
            "assigned": True, "phase": "executing", "nearest_zone": "zone_14",
            "holding_since_game_time": 135,
        }}},
    )

    row = combat_summary(snapshot, manager)["group_3"]

    assert row["style"] == "defend"
    assert row["phase"] == "holding_after_attack"
    assert row["attack_result"] == "target_confirmed_clear"
    assert row["holding_seconds"] == 45
    assert row["order_transition"] == {
        "from": "attack", "to": "defend",
        "reason": "target_confirmed_clear", "automatic": True,
    }
    text = "\n".join(observation_lines(SimpleNamespace(
        to_dict=lambda: {"combat": {"group_3": row}},
    )))
    assert "group_3: roach 5, zergling 6" in text
    assert "Nearest zone: zone_14" in text
    assert "Current activity: holding at zone_14" in text
    assert "Holding duration: 45 s" in text
    assert "Previous order: attack zone_14 — completed" in text
    assert "Current order: defend zone_14" not in text

    detail = render_tool_result("query_army_state", {
        "as_of_game_time": 120,
        "groups": {"group_3": row},
    })
    assert "group_3: roach x5, zergling x6" in detail
    assert "Nearest zone: zone_14" in detail
    assert "Current activity: holding at zone_14" in detail
    assert "Holding duration: 45 s" in detail
    assert "Previous order: attack zone_14 — completed" in detail
    assert "style=defend" not in detail


def test_fake_public_context_keeps_observations_unknown_and_rules_explicit():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        env.backend.units["marine"] = 2
        obs, _, _, _ = drive(env, [
            {"name": "dispatch_group", "arguments": {
                'source': "group_0", 'order': "attack",
                'target': "zone_15", 'units': {"marine": 2},
            }},
            {"name": "advance", "arguments": {'seconds': 1}},
        ])
        row = obs.combat["group_1"]
        assert row["phase"] == "executing"
        assert row["visible_enemy_nearby"] is None
        assert row["weapon_cooldown_active_count"] is None
        text = "\n".join(observation_lines(obs))
        assert "Current order: attack zone_15 — active" in text
        assert "Current order duration: 1 s" in text
        assert "Outbound groups: 1" in render_brief_observation_text(obs.to_dict())
        assert "Visible enemies within 15 of on-map members: unknown" in text
        prompt = LLMAdapter(race=env.config.race if env.config else "terran").system_prompt()
        assert "objectives rather than search waypoints" not in prompt
        from sc2bench_env.interface.platform_rules import ACTION_RULES
        assert "does not create a group" in ACTION_RULES["attack_group"]
        assert "whether or not the zone is currently visible" in ACTION_RULES["attack_group"]
        from sc2bench_env.interface.platform_rules import COMBAT_STYLE_RULES
        assert "Acceptance is not arrival or success" in COMBAT_STYLE_RULES
    finally:
        env.close()
