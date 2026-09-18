"""Race observation builders only project facts and never execute commands."""

import copy

import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.base import BackendSnapshot
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.observations import render_observation_text
from sc2bench_env.interface.race_views import RaceObservationView, build_race_view
from sc2bench_env.runtime.task import Demand, DemandState


def scout():
    return Demand(action="scout", target="", route=("zone_3", "zone_1"),
                  state=DemandState.IN_PROGRESS, demand_id="test-scout")


def test_known_terran_ability_and_scout_output_matches_existing_contract():
    snapshot = BackendSnapshot(
        buildings={"orbital_command": 2},
        info={"scan_ready": 1, "mule_ready": 2, "orbital_energies": [49.0, 100.0],
              "scout_progress": {"moving_to": "zone_1", "waypoint_index": 1, "assigned": True}},
    )
    view = build_race_view(race="terran", snapshot=snapshot, scout_demand=scout())
    assert view.abilities == {"scan_ready": 1, "mule_ready": 2, "orbital_energies": [49.0, 100.0]}
    assert view.legacy_attributes == {"orbital_count": 2, "scan_ready": 1, "mule_ready": 2}
    assert view.scouting == {"scv": {"status": "in_progress", "route": ["zone_3", "zone_1"],
                                    "moving_to": "zone_1", "waypoint_index": 1, "assigned": True}}


def test_absent_ability_keys_keep_existing_compatibility_defaults():
    view = build_race_view(race="terran", snapshot=BackendSnapshot())
    assert view.abilities == {"scan_ready": 0, "mule_ready": 0, "orbital_energies": []}
    assert view.legacy_attributes == {"orbital_count": 0, "scan_ready": 0, "mule_ready": 0}
    assert view.scouting == {}
    view = build_race_view(race="terran", snapshot=BackendSnapshot(info={"scan_ready": 2}))
    assert view.abilities["mule_ready"] == 2


def test_explicit_unknown_ability_facts_are_not_coerced_to_zero_or_empty():
    snapshot = BackendSnapshot(info={"orbital_count": None, "scan_ready": None,
                                     "mule_ready": None, "orbital_energies": None})
    view = build_race_view(race="terran", snapshot=snapshot)
    assert view.abilities == {"scan_ready": None, "mule_ready": None, "orbital_energies": None}
    assert view.legacy_attributes == {"orbital_count": None, "scan_ready": None, "mule_ready": None}


def test_explicit_unknown_scout_progress_is_not_false_or_zero():
    snapshot = BackendSnapshot(info={"scout_progress": {"assigned": None, "waypoint_index": None}})
    row = build_race_view(race="terran", snapshot=snapshot, scout_demand=scout()).scouting["scv"]
    assert row["assigned"] is None and row["waypoint_index"] is None
    assert "moving_to" not in row


def test_stale_backend_scout_progress_does_not_create_a_task():
    snapshot = BackendSnapshot(info={"scout_progress": {"assigned": True, "moving_to": "zone_2"}})
    assert build_race_view(race="terran", snapshot=snapshot).scouting == {}


def test_builder_neither_mutates_backend_facts_nor_changes_requested_route():
    snapshot = BackendSnapshot(info={"orbital_energies": [100.0], "scout_progress": {"assigned": False}})
    demand = scout()
    original_snapshot, original_demand = copy.deepcopy(snapshot), copy.deepcopy(demand)
    view = build_race_view(race="terran", snapshot=snapshot, scout_demand=demand)
    view.abilities["orbital_energies"].clear()
    view.scouting["scv"]["route"].reverse()
    assert snapshot == original_snapshot and demand == original_demand


@pytest.mark.parametrize("race", ["protoss", "zerg", "random"])
def test_unimplemented_view_does_not_fall_back_to_terran(race):
    with pytest.raises(ValueError, match="Unsupported own race"):
        build_race_view(race=race, snapshot=BackendSnapshot(), scout_demand=scout())


def test_environment_uses_selected_builder_instead_of_terran_field_names(monkeypatch):
    import sc2bench_env.env as env_module

    calls = []

    def builder(*, race, snapshot, scout_demand):
        calls.append((race, scout_demand))
        return RaceObservationView(abilities={"fixture_capability": 3},
                                   scouting={"fixture_scout": {"route": ["zone_2"]}})

    env = Environment(record_trajectory=False)
    try:
        env.reset(EpisodeConfig())
        monkeypatch.setattr(env_module, "build_race_view", builder)
        observation = env._build_observation(env.backend.snapshot())
        assert calls == [("terran", None)]
        assert observation.abilities == {"fixture_capability": 3}
        assert observation.scouting == {"fixture_scout": {"route": ["zone_2"]}}
        assert "orbital_energies" not in observation.abilities
    finally:
        env.close()


def test_unknown_race_fields_render_as_unknown_and_legacy_counts_stay_python_only():
    env = Environment(record_trajectory=False)
    try:
        env.reset()
        env.task_manager.submit_decision(
            [{"action": "scout", "route": ["zone_3", "zone_1"]}, {"action": "wait"}], game_time=0,
        )
        snapshot = env.backend.snapshot()
        snapshot.info.update({"orbital_count": None, "scan_ready": None, "mule_ready": None,
                              "orbital_energies": None,
                              "scout_progress": {"assigned": None, "waypoint_index": None}})
        observation = env._build_observation(snapshot)
        text = render_observation_text(observation.to_dict())
        for field in ("Scan ready", "Mule ready", "Orbital energies", "Assigned", "Waypoint index"):
            assert field + ": unknown" in text
        assert observation.orbital_count is None
        assert "orbital_count" not in observation.to_dict()
        assert "orbital_count" not in observation.abilities
    finally:
        env.close()
