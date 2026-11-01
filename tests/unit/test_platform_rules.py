"""Whole-game onboarding rules and their connection to the actual public API."""
from tests.helpers.drive import drive
from sc2bench_env.adapters.llm import LLMAdapter
from agents.llm_agent.prompts import example_prompt_parts, platform_turn_messages

import json

from sc2bench_env import Environment
from sc2bench_env.catalog.registry import get_target, render_system_prompt
from sc2bench_env.interface.actions import parse_decision
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.recording.reader import read_episode
from sc2bench_env.interface.platform_rules import (
    ACTION_RULES, AGENT_GUIDANCE, ARMY_RULES, CONTROL_RULES, DECISION_REQUEST,
    EXECUTION_RULES, GAME_RULES, INTERACTION_RULES, PLANNING_RULES, ROLE_RULES,
)


def test_fixed_rules_are_rendered_once_in_reading_order():
    prompt = render_system_prompt()
    sections = (
        ROLE_RULES, GAME_RULES, INTERACTION_RULES, CONTROL_RULES,
        PLANNING_RULES, EXECUTION_RULES, ARMY_RULES,
    )
    for section in sections:
        assert prompt.count(section) == 1
    assert [prompt.index(section) for section in sections] == sorted(
        prompt.index(section) for section in sections
    )


def test_prompt_teaches_game_basics_without_preset_strategy_or_duration():
    prompt = render_system_prompt()
    for phrase in (
        "time limit results in a Tie", "total supply is capped at 200",
        "unfinished ones provide no supply", "can each use one add-on",
        "compatible producer", "available mineral or gas worker capacity",
    ):
        assert phrase.lower() in prompt.lower()
    for old_contract in ("Strategy.md", "to_count", "set_wake_event", "move_group", "1800-second"):
        assert old_contract not in prompt


def test_missing_visible_enemies_has_one_search_hint_not_guaranteed_hidden_base():
    prompt = render_system_prompt()
    hint = "Fog may hide surviving enemies"
    assert hint in ROLE_RULES
    assert prompt.count(hint) == 1
    assert hint not in DECISION_REQUEST
    assert "flying structures" in ROLE_RULES
    assert "Observation.terminated/result" in ROLE_RULES
    for invented_fact in ("there is a base in", "zone_1", "remaining_enemy_count"):
        assert invented_fact not in ROLE_RULES


def test_search_hint_does_not_change_unknown_owner_or_fog_facts():
    import copy
    from sc2bench_env.adapters.llm.observation import platform_messages
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
    assert "Fog may hide surviving enemies" in messages[0]["content"]
    assert observation == before


def test_prompt_explains_partial_rejection_and_whole_episode_loop():
    for phrase in ("Repeating one requests additional work", "Changes reports execution progress, blockers"):
        assert phrase in EXECUTION_RULES
    assert "call advance by itself" in INTERACTION_RULES
    assert "Query and Action calls made before advance use the same frozen decision snapshot" in INTERACTION_RULES
    assert "Use Read or Knowledge tools" in AGENT_GUIDANCE
    assert "count is the minimum number of additional target units" in ACTION_RULES["train"]
    assert "Supply Depots, MULEs, Supply Drops, town-hall morphs" in CONTROL_RULES
    assert "do not automatically reinforce outbound groups" in ARMY_RULES
    assert "group_0 is the permanent home pool" in ARMY_RULES
    assert "available counts for dispatch or reinforcement" in ARMY_RULES


def test_combat_catalog_explains_actual_styles_without_promising_success():
    assert "local micro" in get_target("attack").description
    assert "safety withdrawal" not in get_target("attack").description
    assert "does not pursue" in get_target("defend").description.lower() or "hold" in get_target("defend").description.lower()
    for old in ("harass", "pressure", "assault", "contain"):
        assert get_target(old) is None


def test_no_new_commands_example_parses_and_preserves_accepted_demand():
    example = [{"name": "advance", "arguments": {'seconds': 5}}]
    assert "call advance by itself" in INTERACTION_RULES
    batch = parse_decision(example)
    assert not batch.actions
    assert batch.advance.seconds == 5
    env = Environment(record_trajectory=False)
    try:
        env.reset()
        drive(env, [{"name": "train", "arguments": {'target': "marine", 'count': 8}}, *example])
        original = list(env.task_manager.demands)
        drive(env, example)
        assert list(env.task_manager.demands) == original
        demand = env.task_manager.demands[original[0]]
        assert demand.count == 8 and demand.is_active
    finally:
        env.close()


def test_repeated_train_really_appends_instead_of_setting_absolute_total():
    env = Environment(record_trajectory=False)
    try:
        env.reset()
        decision = [{"name": "train", "arguments": {'target': "marine", 'count': 8}}, {"name": "advance", "arguments": {'seconds': 5}}]
        drive(env, decision)
        drive(env, decision)
        demands = env.task_manager.active_demands()
        assert len(demands) == 2
        assert sum(d.count for d in demands) == 16
    finally:
        env.close()


def test_prompt_context_and_saved_export_share_rules_but_episode_facts_stay_dynamic(tmp_path):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(enemy_race="zerg", game_time_limit_seconds=321))
        race = env.config.race if env.config else "terran"
        prompt = LLMAdapter(race=race).system_prompt()
        messages = platform_turn_messages(env.latest_observation, env.latest_feedback, race)
        assert messages[0] == {
            "role": "system",
            "content": LLMAdapter(example_prompt_parts(race), race=race).system_prompt(),
        }
        assert read_episode(env.record_path)["platform_prompt"] == prompt
        assert "enemy race zerg" in messages[1]["content"]
        assert "Time 00:00/05:21" in messages[1]["content"]
        assert "[Decision Request]" not in messages[1]["content"]
        assert "[Decision]" in messages[1]["content"]
        assert "321" not in prompt
        drive(env, [{"name": "advance", "arguments": {'seconds': 5}}])
        user = platform_turn_messages(env.latest_observation, env.latest_feedback, env.config.race if env.config else "terran")[1]["content"]
        assert "[Previous Feedback]" in user
        assert "[Decision Request]" not in user
        assert "own race terran" in user
        assert "enemy race zerg" in user
    finally:
        env.close()


def test_terminal_context_does_not_request_another_model_action():
    env = Environment(record_trajectory=False)
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=1))
        obs, _, terminated, _ = drive(env, [{"name": "advance", "arguments": {'seconds': 5}}])
        assert obs.terminated and terminated
        assert "[Decision Request]" not in platform_turn_messages(env.latest_observation, env.latest_feedback, env.config.race if env.config else "terran")[1]["content"]
    finally:
        env.close()
