"""Whole-game onboarding rules and their connection to the actual public API."""

import json

from sc2bench_env import Environment
from sc2bench_env.interface.action_catalog import get_target, render_system_prompt
from sc2bench_env.interface.actions import parse_decision
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.recording.reader import read_episode
from sc2bench_env.interface.platform_rules import (
    ACTION_RULES, ARMY_RULES, CONTROL_RULES, DECISION_REQUEST, FINAL_CHECK,
    GAME_RULES, INTERACTION_RULES, ROLE_RULES,
)


def test_fixed_rules_are_rendered_once_in_reading_order():
    prompt = render_system_prompt()
    sections = (ROLE_RULES, GAME_RULES, CONTROL_RULES, INTERACTION_RULES)
    for section in sections:
        assert prompt.count(section) == 1
    assert prompt.index(ROLE_RULES) < prompt.index(CONTROL_RULES) < prompt.index(INTERACTION_RULES) < prompt.index(GAME_RULES)
    assert FINAL_CHECK not in prompt  # Output rule has one owner in Interaction protocol.


def test_prompt_teaches_game_basics_without_preset_strategy_or_duration():
    prompt = render_system_prompt()
    for phrase in (
        "remaining structures", "Tie with end_reason=time_limit", "Resources are finite",
        "normal supply cap is 200", "unfinished Supply Depots", "at most one add-on",
        "A Reactor does not replace a Tech Lab", "Invisible enemies require detection",
        "more production buildings need income", "unimplemented races/operations remain unavailable",
    ):
        assert phrase.lower() in prompt.lower()
    for old_contract in ("Strategy.md", "to_count", "set_wake_event", "move_group", "1800-second"):
        assert old_contract not in prompt


def test_missing_visible_enemies_has_one_search_hint_not_guaranteed_hidden_base():
    prompt = render_system_prompt()
    hint = "If none are visible and play continues, scout fogged/unconfirmed areas for possible remaining structures; never assume their locations."
    assert hint in ROLE_RULES
    assert prompt.count(hint) == 1
    assert hint not in DECISION_REQUEST
    assert "hidden/flying ones" in ROLE_RULES
    assert "Only Observation.terminated and the reported result" in ROLE_RULES
    for invented_fact in ("there is a base in", "zone_1", "remaining_enemy_count"):
        assert invented_fact not in ROLE_RULES
    assert "does not automatically become a whole-map search" in ARMY_RULES


def test_search_hint_does_not_change_unknown_owner_or_fog_facts():
    import copy
    from sc2bench_env.recording.context import platform_messages
    observation = {
        "terminated": False,
        "zone_state": [{
            "zone_id": "zone_9", "zone_role": "other_expansion",
            "known_owner": "unconfirmed", "vision_state": "fogged",
            "own_contents": {"units": {}, "buildings": {}},
            "visible_enemy_contents": {"units": {}, "buildings": {}},
            "last_seen_enemy_contents": {"units": {}, "buildings": {}},
            "enemy_information_age_seconds": None,
            "visible_enemy_weapon_in_range": None,
        }],
    }
    before = copy.deepcopy(observation)
    messages = platform_messages(render_system_prompt(), observation)
    assert "possible remaining structures" in messages[0]["content"]
    assert "zone_9 | other_expansion | unconfirmed | fogged" in messages[1]["content"]
    assert "ENEMY visible: none | ENEMY last seen: none" in messages[1]["content"]
    assert observation == before


def test_prompt_explains_partial_rejection_and_whole_episode_loop():
    for phrase in (
        "Omission keeps it, even if blocked", "soft-reserve", "blocking mode",
        "optional continuous mode", "correct only the rejected work",
        "positive interval-only wait", "until Observation.terminated",
        "external harness handles shutdown and model/API failures",
    ):
        assert phrase in INTERACTION_RULES
    assert "N additional births" in ACTION_RULES["train"]
    assert "Combat cannot bind future train orders" in ACTION_RULES["train"]
    assert "AutoDepot is disabled" in CONTROL_RULES
    assert "automatically rebuild destroyed production" in CONTROL_RULES
    assert "Its members remain dispatchable even during home defense" in ARMY_RULES
    assert "never automatically reinforce outbound groups" in ARMY_RULES


def test_combat_catalog_explains_actual_styles_without_promising_success():
    assert "safety withdrawal" in get_target("attack").description
    assert "no automatic map search or reinforcements" in get_target("attack").description
    assert "bounded chasing" in get_target("defend").description
    for old in ("harass", "pressure", "assault", "contain"):
        assert get_target(old) is None


def test_no_new_commands_example_parses_and_preserves_accepted_demand():
    example = [{"action": "wait"}]
    assert json.dumps(example, separators=(",", ":")) in INTERACTION_RULES
    batch = parse_decision(example)
    assert not batch.actions
    assert batch.wait.any_of[0].condition == "interval"
    env = Environment(record_trajectory=False)
    try:
        env.reset()
        env.step([{"action": "train", "target": "marine", "count": 8}, *example])
        original = list(env.task_manager.demands)
        env.step(example)
        assert list(env.task_manager.demands) == original
        demand = env.task_manager.demands[original[0]]
        assert demand.count == 8 and demand.is_active
    finally:
        env.close()


def test_repeated_train_really_appends_instead_of_setting_absolute_total():
    env = Environment(record_trajectory=False)
    try:
        env.reset()
        decision = [{"action": "train", "target": "marine", "count": 8}, {"action": "wait"}]
        env.step(decision)
        env.step(decision)
        demands = env.task_manager.active_demands()
        assert len(demands) == 2
        assert sum(d.count for d in demands) == 16
    finally:
        env.close()


def test_prompt_context_and_saved_export_share_rules_but_episode_facts_stay_dynamic(tmp_path):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(enemy_race="zerg", game_time_limit_seconds=321))
        prompt = env.get_system_prompt()
        messages = env.get_context()
        assert messages[0] == {"role": "system", "content": prompt}
        assert read_episode(env.record_path)["platform_prompt"] == prompt
        assert "Enemy race: zerg" in messages[1]["content"]
        assert "Time limit: 05:21 (321 s)" in messages[1]["content"]
        assert messages[1]["content"].endswith(DECISION_REQUEST)
        assert "321" not in prompt
        env.step([{"action": "wait"}])
        user = env.get_context()[1]["content"]
        assert user.index("[Previous Feedback]") < user.index("[Decision Request]")
        assert user.endswith(DECISION_REQUEST)
    finally:
        env.close()


def test_terminal_context_does_not_request_another_model_action():
    env = Environment(record_trajectory=False)
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=1))
        obs, _, terminated, _ = env.step([{"action": "wait"}])
        assert obs.terminated and terminated
        assert "[Decision Request]" not in env.get_context()[1]["content"]
    finally:
        env.close()
