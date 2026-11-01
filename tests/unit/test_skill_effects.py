"""Skill orders and normal damage cannot substitute for observed impact."""
import pytest

from tests.helpers.skill_effects import (
    ObservedImpact, ObservedSustainedLock, ObservedFormRoundTrip, ObservedTeleport, ObservedLockCycle,
)


@pytest.mark.parametrize("burst", [100, 200])
def test_damage_burst_requires_same_target_engine_order(burst):
    tracker = ObservedImpact(burst)
    tracker.sample(0, {1: 500}, [1])
    tracker.sample(.5, {1: 500 - burst})
    assert tracker.hits == {1}


@pytest.mark.parametrize("case", ["order_only", "ordinary_fire", "wrong_target", "disappeared",
                                  "expired", "large_sample_gap", "no_order"])
def test_false_impact_evidence_is_rejected(case):
    tracker = ObservedImpact(100)
    tracker.sample(0, {1: 500, 2: 500}, [] if case == "no_order" else [1])
    if case == "ordinary_fire":
        for i in range(1, 11):
            tracker.sample(i * .5, {1: 500 - i * 20})
    elif case == "order_only":
        tracker.sample(.5, {1: 500})
    elif case == "wrong_target":
        tracker.sample(.5, {1: 500, 2: 300})
    elif case == "disappeared":
        tracker.sample(.5, {})
        tracker.sample(1, {1: 300})
    elif case == "expired":
        tracker.sample(6, {1: 500})
        tracker.sample(6.5, {1: 300})
    else:
        tracker.sample(2 if case == "large_sample_gap" else .5, {1: 300})
    assert not tracker.hits


def test_lock_requires_continuous_buff_and_real_damage():
    tracker = ObservedSustainedLock()
    tracker.sample(0, {1: 200}, [1])
    tracker.sample(1, {1: 190}, [1])
    tracker.sample(2, {1: 180}, [1])
    tracker.sample(3, {1: 170}, [1])
    assert tracker.sustained == {1}


@pytest.mark.parametrize("case", ["transient", "buff_lost", "new_target", "no_damage", "sample_gap"])
def test_lock_labels_cannot_substitute_for_sustained_effect(case):
    tracker = ObservedSustainedLock()
    tracker.sample(0, {1: 200}, [1])
    tracker.sample(1, {1: 190}, [] if case == "buff_lost" else [1])
    if case not in {"buff_lost", "sample_gap"}:
        tracker.sample(2, {1: 180}, [1])
    if case == "new_target":
        tracker.sample(3, {2: 170}, [2])
    else:
        tracker.sample(2 if case == "transient" else 3,
                       {1: 200 if case == "no_damage" else 170}, [1])
    assert not tracker.sustained


def test_same_unit_form_round_trip_requires_ordered_states():
    tracker = ObservedFormRoundTrip("mobile", "deployed")
    for form in ("mobile", "mobile", "deployed", "deployed", "mobile"):
        tracker.sample(1, form)
    assert tracker.tag == 1 and tracker.stage == 2


@pytest.mark.parametrize("samples", [
    [(1, "deployed"), (1, "mobile")],
    [(1, "mobile"), (1, "deployed")],
    [(1, "mobile"), (2, "deployed"), (1, "mobile")],
    [(1, "mobile"), (1, "deployed"), (2, "mobile")],
    [(1, "mobile"), (1, "mobile")],
])
def test_form_presence_or_replacement_cannot_substitute_for_round_trip(samples):
    tracker = ObservedFormRoundTrip("mobile", "deployed")
    for tag, form in samples:
        tracker.sample(tag, form)
    assert tracker.stage != 2


def test_teleport_requires_order_then_same_unit_actual_arrival():
    tracker = ObservedTeleport((0, 0))
    tracker.sample(0, 1, (35, 0), (2, 0))
    tracker.sample(.5, 1, (2, 0))
    assert tracker.arrived


@pytest.mark.parametrize("case", ["order_only", "normal_flight", "wrong_unit", "wrong_destination",
                                  "expired", "sample_gap", "no_order", "near_home"])
def test_order_or_survival_is_not_actual_teleport(case):
    tracker = ObservedTeleport((0, 0))
    start = (5, 0) if case == "near_home" else (35, 0)
    tracker.sample(0, 1, start, None if case == "no_order" else (2, 0))
    if case == "normal_flight":
        for i in range(1, 18):
            tracker.sample(i * .5, 1, (35 - i * 2, 0))
    else:
        tracker.sample(9 if case == "expired" else (2 if case == "sample_gap" else .5),
                       2 if case == "wrong_unit" else 1,
                       start if case == "order_only" else ((10, 0) if case == "wrong_destination" else (2, 0)))
    assert not tracker.arrived


def test_lock_cycle_requires_natural_buff_end_on_living_target():
    tracker = ObservedLockCycle()
    for i in range(28):
        tracker.sample(i * .5, {1: 1500 - i * 10}, [1])
    assert not tracker.completed
    tracker.sample(14, {1: 1200}, [])
    assert tracker.completed


@pytest.mark.parametrize("case", ["short", "death", "different_target", "sample_gap", "no_damage"])
def test_lock_cycle_rejects_premature_or_ambiguous_end(case):
    tracker = ObservedLockCycle()
    for i in range(28 if case != "short" else 7):
        tracker.sample(i * .5, {1: 1500 if case == "no_damage" else 1500 - i * 10}, [1])
    tracker.sample(16 if case == "sample_gap" else (3.5 if case == "short" else 14),
                   {} if case == "death" else ({2: 1200} if case == "different_target" else
                   {1: 1500 if case == "no_damage" else 1200}), [])
    assert not tracker.completed
