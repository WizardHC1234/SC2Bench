"""Clear submission effects without changing canonical execution facts."""

import copy

import pytest

from sc2bench_env.interface.observation_text import priority_lines, render_feedback_text
from sc2bench_env.recording.context import platform_messages


@pytest.mark.parametrize("action,meaning", [
    ("build", "additional construction registered"),
    ("train", "additional production registered"),
    ("research", "research request registered"),
    ("combat", "army order registered"),
    ("retreat", "return order registered, not arrival"),
    ("scan", "cast request registered"),
    ("call_mule", "cast request registered"),
    ("scout", "scouting order registered"),
    ("upgrade", "morph request registered"),
])
def test_accepted_receipt_explains_submission_not_game_completion(action, meaning):
    feedback = {"receipts": [{"action": action, "result": "accepted", "count": 8}]}
    original = copy.deepcopy(feedback)
    text = render_feedback_text(feedback)
    assert f"1. {action}: accepted ({meaning})" in text
    assert "Count: 8" in text
    assert feedback == original


@pytest.mark.parametrize("result,meaning", [
    ("idempotent_noop", "unchanged; no additional work"),
    ("ignored_duplicate_action_id", "retry ignored; no additional work"),
    ("rejected", "not applied"),
])
def test_noop_retry_and_rejection_are_not_new_acceptances(result, meaning):
    text = render_feedback_text({"receipts": [{"action": "research", "result": result}]})
    assert meaning in text
    assert "request registered" not in text


@pytest.mark.parametrize("quantity", [0, 3])
def test_cancellation_explains_removed_quantity_preserving_original_reason(quantity):
    feedback = {"receipts": [{"action": "cancel", "result": "accepted", "target_action": "train",
                              "target": "marine", "reason": f"cleared_waiting={quantity}"}]}
    original = copy.deepcopy(feedback)
    text = render_feedback_text(feedback)
    assert "cancellation processed" in text
    assert f"unstarted work quantity removed: {quantity}" in text
    assert "Target action: train" in text
    assert feedback == original


def test_invalid_batch_scope_is_visible_even_when_event_is_already_shown():
    event = {"type": "decision_rejected", "reason": "invalid fields"}
    observation = {"terminated": False, "recent_events": [event]}
    feedback = {"receipts": [], "events": [event]}
    original = copy.deepcopy((observation, feedback))
    user = platform_messages("rules", observation, feedback)[1]["content"]
    assert "no entries from this array applied; previously accepted work continues" in user
    assert user.count("Reason: invalid fields") == 1
    assert (observation, feedback) == original


def test_partial_production_remains_full_per_order_and_unknown_is_not_zero():
    rows = [
        {"action": "train", "target": "marine", "remaining": 6, "order_progress": "2/8",
         "in_production": 3, "waiting_to_produce": 3, "state": "in_production", "waiting_for": "resources"},
        {"action": "train", "target": "marine", "remaining": 8, "order_progress": "0/8",
         "in_production": None, "waiting_to_produce": None, "state": "in_production", "waiting_for": None},
        {"action": "build", "target": "barracks", "remaining": 1, "state": "worker_en_route"},
    ]
    original = copy.deepcopy(rows)
    text = "\n".join(priority_lines(rows))
    assert "1 | train | marine | 6 | 2/8 | 3 | 3 | production active" in text
    assert "2 | train | marine | 8 | 0/8 | unknown | unknown | production active | unknown" in text
    assert "resource budget unavailable (including earlier spending priority)" in text
    assert "worker travelling | not reported" in text
    assert rows == original


@pytest.mark.parametrize("state", ["waiting_to_start", "under_construction", "in_progress"])
def test_active_phases_are_readable_without_inventing_finished_work(state):
    text = "\n".join(priority_lines([{"action": "research", "target": "stimpack", "remaining": 1,
                                     "state": state, "waiting_for": "prerequisite:barracks_techlab"}]))
    assert "requires ready barracks_techlab" in text
    assert "command completed" not in text
    assert "1 | research | stimpack | 1" in text


def test_unrecognized_results_and_phases_preserve_forward_compatible_facts():
    feedback = {"receipts": [{"action": "future_action", "result": "future_result", "reason": "future_reason"}]}
    assert "future_result" in render_feedback_text(feedback)
    assert "future_reason" in render_feedback_text(feedback)
    text = "\n".join(priority_lines([{"action": "build", "target": "future_target", "state": "future_phase"}]))
    assert "future_phase" in text and "future_target" in text
