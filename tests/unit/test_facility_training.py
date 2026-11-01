"""Facility-local demand facts do not create orders or hide other views."""
import copy

import pytest

from sc2bench_env.interface.observation_text import facility_training
from sc2bench_env.interface.observations import render_observation_text


def test_training_groups_by_catalog_producer_not_living_units_or_order_progress():
    training = {"marine": {"in_production": 2, "waiting_to_produce": 8, "order_progress": "3/13"},
                "siege_tank": {"in_production": 1, "waiting_to_produce": 2},
                "medivac": {"in_production": 1, "waiting_to_produce": 2},
                "viking": {"in_production": 0, "waiting_to_produce": 1},
                "scv": {"in_production": 1, "waiting_to_produce": 4}}
    original = copy.deepcopy(training)
    assert facility_training("barracks", training) == "marine — paid 2, waiting 8"
    assert facility_training("factory", training) == "siege_tank — paid 1, waiting 2"
    assert facility_training("starport", training) == "medivac — paid 1, waiting 2; viking — paid 0, waiting 1"
    assert facility_training("command_center", training) == "scv — paid 1, waiting 4"
    assert training == original


def test_orders_remain_visible_without_ready_producer_and_with_missing_technology():
    observation = {"production": [{"facility": "factory", "ready_grounded": 0}],
                   "training": {"siege_tank": {"in_production": 0, "waiting_to_produce": 3,
                                              "waiting_for": "prerequisite:factory_techlab"}},
                   "building": {}}
    text = render_observation_text(observation)
    assert "[Production Capacity]\nnone" in text
    assert "Unit technology" not in text
    assert "[Training]" in text and "siege_tank | 0 | 3" in text
    assert "requires ready factory_techlab" in text


def test_real_idle_facilities_have_no_matching_orders_even_with_marine_backlog():
    observation = {"production": [
        {"facility": "factory", "ready_grounded": 4, "free_slots": 4, "techlab_hosts": 0},
        {"facility": "starport", "ready_grounded": 2, "free_slots": 2, "techlab_hosts": 1}],
        "training": {"marine": {"in_production": 3, "waiting_to_produce": 12}},
        "building": {}}
    original = copy.deepcopy(observation)
    text = render_observation_text(observation)
    capacity = text.split("[Production Capacity]\n", 1)[1].split("\n\n[Building]", 1)[0]
    assert "factory | 4" in capacity
    assert "starport | 2" in capacity
    assert "Unit technology" not in capacity
    assert "marine — paid" not in capacity
    assert "Accepted training:" not in capacity
    assert observation == original


@pytest.mark.parametrize("training,expected", [
    (None, "unknown"),
    ({}, "none"),
    ({"marine": {"in_production": 0, "waiting_to_produce": 0}}, "none"),
    ({"marine": {"in_production": None, "waiting_to_produce": 2}}, "marine — paid unknown, waiting 2"),
    ({"marine": {"in_production": 1}}, "marine — paid 1, waiting unknown"),
    ({"marine": {"in_production": True, "waiting_to_produce": -1}}, "marine — paid unknown, waiting unknown"),
])
def test_missing_unknown_and_zero_are_distinct(training, expected):
    assert facility_training("barracks", training) == expected


def test_zerg_larva_shows_larva_trains_and_hatchery_shows_queens():
    training = {
        "zergling": {"in_production": 2, "waiting_to_produce": 4},
        "queen": {"in_production": 1, "waiting_to_produce": 0},
        "baneling": {"in_production": 1, "waiting_to_produce": 0},
    }
    assert facility_training("larva", training, race="zerg") == "zergling — paid 2, waiting 4"
    assert facility_training("hatchery", training, race="zerg") == "queen — paid 1, waiting 0"
    assert facility_training("zergling", training, race="zerg") == "baneling — paid 1, waiting 0"
    assert "unknown" not in facility_training("larva", {}, race="zerg")


def test_unknown_catalog_targets_are_not_silently_assigned_or_reported_as_none():
    training = {"new_unit": {"in_production": 1, "waiting_to_produce": 2}}
    assert facility_training("factory", training) == "unmapped training targets: new_unit (producer unknown)"
    assert facility_training("warpgate", training) == "unknown (producer type not in current catalog)"


def test_capacity_does_not_repeat_training_orders():
    observation = {"production": [
        {"facility": "factory", "ready_grounded": 1, "techlab_hosts": 1},
        {"facility": "starport", "ready_grounded": 1, "techlab_hosts": 0}],
        "training": {"medivac": {"in_production": 1, "waiting_to_produce": 2}}, "building": {}}
    text = render_observation_text(observation)
    block = text.split("[Production Capacity]\n", 1)[1].split("\n\n[Building]", 1)[0]
    assert "factory | 1 | 1" in block
    assert "starport | 1 | 0" in block
    assert "tech ready:" not in block
    assert "medivac" not in block
    assert "medivac | 1 | 2" in text
    assert "Accepted training:" not in block
    assert "does not mean affordable or an available slot" not in block
