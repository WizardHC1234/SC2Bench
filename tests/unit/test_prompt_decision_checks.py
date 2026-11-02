"""Short decision checks guide reading, not strategy or execution."""
from tests.helpers.drive import drive
from agents.llm_agent.prompts import platform_turn_messages

import copy
import json

from sc2bench_env import Environment
from sc2bench_env.catalog.registry import render_system_prompt
from sc2bench_env.interface.platform_rules import AGENT_GUIDANCE, DECISION_REQUEST, INTERACTION_RULES
from sc2bench_env.adapters.llm.observation import platform_messages


def test_fixed_prompt_shrinks_without_moving_checks_into_every_section():
    prompt = render_system_prompt()
    assert 4000 < len(prompt) < 9000
    assert "Call advance exactly once as the final tool call" in prompt
    assert len(DECISION_REQUEST) < 450
    assert '"target":"<building_or_addon_name>"' not in prompt
    assert "wait has no seconds or interval field" not in prompt
    assert "call advance by itself" in prompt


def test_checks_use_actual_inventory_and_existing_orders_not_a_build_plan():
    for phrase in ("Actions and advance may share a reply if advance is last",
                   "inspect results or make corrections first", "call advance by itself"):
        assert phrase in INTERACTION_RULES
    assert "Query and Action calls made before advance" in INTERACTION_RULES
    assert "Use Read or Knowledge tools" in AGENT_GUIDANCE
    assert "do not resubmit" not in DECISION_REQUEST
    prompt = render_system_prompt()
    for phrase in ("do not automatically reinforce", "group_0 is the permanent home pool",
                   "Accepted build and train requests persist"):
        assert phrase in prompt
    for anchor in ("marine", "siege_tank", "medivac", "zone_1", "group_1",
                   "must mix", "always expand", "always build", '"count":'):
        assert anchor not in DECISION_REQUEST


def test_checks_follow_facts_and_feedback_once_without_changing_them():
    observation = {
        "terminated": False,
        "own_forces": {"marine": {"living": 2, "free": 0, "assigned": 2}},
        "training": {"marine": {"in_production": 0, "waiting_to_produce": 12}},
        "recent_events": [],
    }
    feedback = {"receipts": [{"name": "combat", "arguments": {"target": "zone_12"},
                              "result": "rejected", "reason": "insufficient_units"}], "events": []}
    before = copy.deepcopy((observation, feedback))
    messages = platform_messages(render_system_prompt(), observation, feedback)
    user = messages[1]["content"]
    assert DECISION_REQUEST not in user
    assert "[Decision Request]" not in user
    assert user.index("[Current Observation]") < user.index("[Previous Feedback]")
    assert "[Decision]" not in user
    assert (observation, feedback) == before


def test_checklist_does_not_add_orders_or_dedupe_intentional_repeats(tmp_path):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset()
        decision = [{"name": "train", "arguments": {'target': "marine", 'count': 8}},
                    {"name": "advance", "arguments": {'seconds': 5}}]
        drive(env, decision)
        previous = copy.deepcopy(env.task_manager.production_priority_summary())
        messages = platform_turn_messages(env.latest_observation, env.latest_feedback, env.config.race if env.config else "terran")
        assert "[Decision Request]" not in messages[1]["content"]
        assert "[Decision]" not in messages[1]["content"]
        assert env.task_manager.production_priority_summary() == previous
        reply = [{"name": "advance", "arguments": {"seconds": 5}}]
        drive(env, reply, agent_context={"messages": messages, "assistant_content": json.dumps(reply)})
        assert len(env.task_manager.active_demands()) == 1
        drive(env, decision)
        assert sum(d.count for d in env.task_manager.active_demands()) == 16
        from sc2bench_env.recording.reader import read_episode
        exported = read_episode(env.record_path)
        assert exported["messages"] == messages
    finally:
        env.close()


def test_terminal_context_does_not_request_checks_or_new_decisions():
    user = platform_messages("rules", {"terminated": True})[1]["content"]
    assert DECISION_REQUEST not in user
    assert "[Decision Request]" not in user
