"""Observation race selects reference facts without restricting raw data."""

import copy
from types import SimpleNamespace

import pytest

from sc2bench_env.interface import catalogs
from sc2bench_env.interface.catalog_types import TargetSpec
from sc2bench_env.interface.observation_text import (
    _production_unit_technology, facility_training, production_options,
)
from sc2bench_env.interface.observations import (
    GameView, Observation, OwnForcesView, render_observation_text,
)


def terran_observation():
    return {
        "game": {"race": "terran", "enemy_race": "zerg", "game_time_seconds": 123},
        "production": [
            {"facility": "factory", "ready_grounded": 2, "techlab_hosts": 1,
             "reactor_hosts": 0, "capacity": 2, "occupied_slots": 1,
             "free_slots": 1, "free_techlab_slots": 0},
            {"facility": "starport", "ready_grounded": 1, "techlab_hosts": None,
             "reactor_hosts": 0, "capacity": 1, "occupied_slots": 0,
             "free_slots": 1, "free_techlab_slots": None},
        ],
        "building": {"factory": {"completed": 2}, "starport": {"completed": 1},
                     "armory": {"completed": 0}, "fusion_core": {"completed": None}},
        "training": {"siege_tank": {"in_production": 1, "waiting_to_produce": 2},
                     "medivac": {"in_production": None, "waiting_to_produce": 3}},
        "own_forces": {"workers": {"scv": 12}, "army": {"marine": 8}},
        "terminated": False,
    }


def test_production_rendering_queries_the_canonical_own_race(monkeypatch):
    observation = terran_observation()
    observation["race"] = "protoss"  # Legacy alias cannot override canonical Game.
    calls = []
    original = catalogs.get_catalog

    def lookup(*, race):
        calls.append(race)
        return original(race=race)

    monkeypatch.setattr(catalogs, "get_catalog", lookup)
    text = render_observation_text(observation)
    assert calls and set(calls) == {"terran"}
    assert "Accepted training: siege_tank — paid 1, waiting 2" in text
    assert "Ready attached Tech Labs" in text


@pytest.mark.parametrize("race", ["protoss", "zerg", "unknown", None, ""])
def test_unavailable_catalog_never_falls_back_to_terran_reference(race):
    observation = terran_observation()
    observation["game"]["race"] = race
    observation["race"] = "terran"
    text = render_observation_text(observation)
    capacity = text.split("[Production Capacity]\n", 1)[1].split("\n\n[Building]", 1)[0]
    assert "catalog unavailable for own race" in capacity
    assert "tech ready:" not in capacity
    assert "Accepted training: siege_tank" not in capacity
    assert "Ready attached Tech Labs" not in capacity
    assert "Ready attached Reactors" not in capacity
    assert "Free Tech Lab slots" not in capacity
    # Unavailable reference mapping never removes provided Training facts.
    assert "siege_tank | 1 | 2" in text


def test_generic_observation_stays_renderable_without_invented_capacity_columns():
    observation = {
        "game": {"race": "protoss"},
        "production": [{"facility": "gateway", "capacity": None, "free_slots": 0,
                        "race_specific_fact": 3}],
        "training": {"stalker": {"in_production": 1, "waiting_to_produce": 2}},
        "own_forces": {"workers": {"probe": 16}, "army": {"stalker": 4}},
    }
    before = copy.deepcopy(observation)
    text = render_observation_text(observation)
    assert "Facility | Capacity | Free slots" in text
    assert "gateway | unknown | 0" in text
    assert "Race specific fact: 3" in text
    assert "Ready grounded" not in text and "Occupied slots" not in text
    assert "stalker | 1 | 2" in text and "Living workers: probe 16" in text
    assert observation == before


def test_structured_observation_uses_game_race_not_legacy_attribute():
    observation = Observation(
        game=GameView(race="zerg"),
        production=[{"facility": "hatchery", "capacity": 3}],
        own_forces=OwnForcesView(workers={"drone": 12}),
    )
    text = "\n".join(observation.section_lines())
    assert "Own race: zerg" in text and "Living workers: drone 12" in text
    assert "catalog unavailable for own race zerg" in text
    assert "Ready attached Tech Labs" not in text


def test_legacy_dictionary_without_race_keeps_terran_rendering():
    observation = terran_observation()
    expected = render_observation_text(observation).split("[Economy]", 1)[1]
    del observation["game"]
    assert render_observation_text(observation).split("[Economy]", 1)[1] == expected


def test_explicit_legacy_race_is_used_when_game_race_is_absent():
    observation = terran_observation()
    del observation["game"]["race"]
    observation["race"] = "zerg"
    text = render_observation_text(observation)
    assert "catalog unavailable for own race zerg" in text
    assert "Ready attached Tech Labs" not in text


def test_helpers_preserve_unknown_vs_empty_without_race_reference():
    assert facility_training("gateway", None, race="protoss") == "unknown"
    assert "catalog unavailable" in facility_training("gateway", {}, race="protoss")
    assert production_options([], {}, race="protoss") == []
    assert "catalog unavailable" in production_options([{"facility": "gateway"}], {}, race="protoss")[0]


def test_attached_tech_lab_readiness_rule_is_terran_only(monkeypatch):
    # Synthetic metadata verifies rule isolation, not real Protoss support.
    data = SimpleNamespace(targets=(TargetSpec(
        name="synthetic_unit", action="train", kind="unit", description="fixture",
        produced_at="synthetic_factory", prerequisites=("synthetic_factory", "synthetic_factory_techlab"),
    ),))
    monkeypatch.setattr(catalogs, "get_catalog", lambda *, race: data)
    row = {"facility": "synthetic_factory", "ready_grounded": 1, "techlab_hosts": 1}
    assert _production_unit_technology(row, {}, race="terran") == [("synthetic_unit", ())]
    assert _production_unit_technology(row, {}, race="protoss") == [
        ("synthetic_unit", ("missing ready synthetic_factory_techlab",)),
    ]
