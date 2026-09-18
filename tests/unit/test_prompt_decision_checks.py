"""Short decision checks guide reading, not strategy or execution."""

import copy
import json

from sc2bench_env import Environment
from sc2bench_env.interface.action_catalog import render_system_prompt
from sc2bench_env.interface.platform_rules import DECISION_REQUEST
from sc2bench_env.recording.context import platform_messages


def test_fixed_prompt_shrinks_without_moving_checks_into_every_section():
    prompt = render_system_prompt()
    assert len(prompt) < 21200  # Clear per-action time headings; still below the previous 21,911.
    assert DECISION_REQUEST not in prompt
    assert len(DECISION_REQUEST) < 350
    assert '"target":"<building_or_addon_name>"' in prompt
    assert "wait has no seconds or interval field" in prompt


def test_checks_use_actual_inventory_and_existing_orders_not_a_build_plan():
    for phrase in ("Choose any plan changes", "or wait", "exactly one final wait",
                   "listed fields only"):
        assert phrase in DECISION_REQUEST
    assert "do not resubmit" not in DECISION_REQUEST
    prompt = render_system_prompt()
    for phrase in ("production needs train requests", "free=dispatchable home pool",
                   "not living inventory", "without rebinding", "Fog of war is partial information"):
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
    feedback = {"receipts": [{"action": "combat", "result": "rejected",
                             "reason": "insufficient_units"}], "events": []}
    before = copy.deepcopy((observation, feedback))
    messages = platform_messages(render_system_prompt(), observation, feedback)
    user = messages[1]["content"]
    assert user.count(DECISION_REQUEST) == 1
    assert user.index("[Current Observation]") < user.index("[Previous Feedback]")
    assert user.index("[Previous Feedback]") < user.index("[Decision Request]")
    assert (observation, feedback) == before


def test_checklist_does_not_add_orders_or_dedupe_intentional_repeats(tmp_path):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset()
        decision = [{"action": "train", "target": "marine", "count": 8},
                    {"action": "wait"}]
        env.step(decision)
        previous = copy.deepcopy(env.task_manager.production_priority_summary())
        messages = env.get_context()
        assert messages[1]["content"].endswith(DECISION_REQUEST)
        assert env.task_manager.production_priority_summary() == previous
        reply = '[{"action":"wait"}]'
        env.step(reply, agent_context={"messages": messages, "assistant_content": reply})
        assert len(env.task_manager.active_demands()) == 1
        env.step(decision)
        assert sum(d.count for d in env.task_manager.active_demands()) == 16
        from sc2bench_env.recording.reader import read_episode
        exported = read_episode(env.record_path)
        recorded = [row for row in exported["interactions"] if row.get("input", {}).get("messages") == messages]
        assert recorded
    finally:
        env.close()


def test_terminal_context_does_not_request_checks_or_new_decisions():
    user = platform_messages("rules", {"terminated": True})[1]["content"]
    assert DECISION_REQUEST not in user
    assert "[Decision Request]" not in user
