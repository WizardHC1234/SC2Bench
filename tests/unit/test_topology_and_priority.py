"""Visible spatial relationships and macro ordering without command UUIDs."""
import copy
from types import SimpleNamespace as NS

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.backends.sharpy.zones import ZoneRegistry
from sc2bench_env.backends.sharpy.zone_topology import read_topology
from sc2bench_env.interface.observations import render_observation_text
from sc2bench_env.interface.action_catalog import render_system_prompt
from sc2bench_env.runtime.task_manager import TaskManager
from sc2bench_env.runtime.task import DemandState
from tests.unit.test_zone_registry import Point


def terrain_zones():
    registry = ZoneRegistry()
    zones = [NS(center_location=Point(x + .5, .5), zone_index=i, ramp=None, paths={})
             for i, x in enumerate((0, 30, 60))]
    registry.sync_from_centers([(z.center_location.x, z.center_location.y) for z in zones])
    registry.sync_roles(NS(start_location=zones[0].center_location,
                           enemy_start_locations=[zones[-1].center_location]),
                        NS(own_natural=zones[1], enemy_natural=zones[1]), zones)
    for i, a in enumerate(zones):
        for j, b in enumerate(zones):
            if i == j:
                continue
            start, end = int(a.center_location.x), int(b.center_location.x)
            direction = 1 if end > start else -1
            a.paths[j] = NS(distance=abs(end - start),
                            path=[(x, 0) for x in range(start, end + direction, direction)])
    return registry, zones


def test_topology_distances_and_corridor_links_are_not_numeric_zone_adjacency():
    registry, zones = terrain_zones()
    view = read_topology(registry, zones)
    assert view["verified_path_pair_count"] == view["total_path_pair_count"] == 3
    a, b, c = view["zones"]
    assert a["path_distance_from_own_main"] == 0
    assert c["path_distance_from_own_main"] == 60
    assert a["path_distance_to_enemy_main"] == 60
    assert a["corridor_neighbors"] == [{"zone_id": "zone_1", "path_distance": 30}]
    assert {x["zone_id"] for x in b["corridor_neighbors"]} == {"zone_0", "zone_2"}
    assert c["corridor_neighbors"] == [{"zone_id": "zone_1", "path_distance": 30}]
    text = render_observation_text({"map_topology": view})
    assert "zone_0 | no | 0 | 60 | zone_1 (30)" in text
    assert "{" not in text


def test_topology_cache_survives_sharpy_reorder_with_stale_path_indices():
    registry, zones = terrain_zones()
    before = read_topology(registry, zones)
    cached_links = registry._topology_links
    for i, z in enumerate(reversed(zones)):
        z.zone_index = i
    after = read_topology(registry, list(reversed(zones)))
    assert after == before
    assert registry._topology_links is cached_links


def test_initial_stale_path_mapping_is_rejected_and_later_valid_paths_retry():
    registry, zones = terrain_zones()
    for i, z in enumerate(reversed(zones)):
        z.zone_index = i
    first = read_topology(registry, zones)
    assert first["zones"][-1]["path_distance_from_own_main"] is None
    for i, z in enumerate(zones):
        z.zone_index = i
    second = read_topology(registry, zones)
    assert second["verified_path_pair_count"] == 3
    assert second["zones"][-1]["path_distance_from_own_main"] == 60


def test_missing_or_empty_paths_are_unknown_not_unreachable_or_euclidean_guess():
    registry, zones = terrain_zones()
    for z in zones:
        z.paths = {}
    zones[0].paths[2] = NS(distance=0, path=[])
    view = read_topology(registry, zones)
    assert view["verified_path_pair_count"] == 0
    assert view["zones"][-1]["path_distance_from_own_main"] is None
    assert all(z["corridor_neighbors"] is None for z in view["zones"])


def test_multispawn_enemy_main_distance_is_not_exposed_before_confirmation():
    registry, zones = terrain_zones()
    registry._roles.pop((60.5, .5))
    view = read_topology(registry, zones)
    assert all(row["path_distance_to_enemy_main"] is None for row in view["zones"])
    registry._roles[(60.5, .5)] = "enemy_main"
    assert read_topology(registry, zones)["zones"][0]["path_distance_to_enemy_main"] == 60


def test_reset_discards_previous_map_topology_and_paths():
    registry, zones = terrain_zones()
    read_topology(registry, zones)
    registry.reset()
    assert registry._topology_paths == registry._topology_links == {}
    assert registry._topology_signature is None


def manager():
    m = TaskManager()
    m.submit_decision([
        {"action": "train", "target": "marine", "count": 8},
        {"action": "build", "target": "barracks"},
        {"action": "train", "target": "marauder", "count": 4},
        {"action": "research", "target": "stimpack"},
        {"action": "train", "target": "marine", "count": 8},
        {"action": "wait"},
    ], game_time=0)
    return m


def test_global_priority_keeps_interleaved_types_and_repeated_orders():
    m = manager()
    rows = m.production_priority_summary()
    assert [(r["action"], r["target"]) for r in rows] == [
        ("train", "marine"), ("build", "barracks"), ("train", "marauder"),
        ("research", "stimpack"), ("train", "marine")]
    text = render_observation_text({"production_priority": rows})
    assert "1 | train | marine | 8 | 0/8 | 0 | 8" in text
    assert "5 | train | marine | 8 | 0/8 | 0 | 8" in text
    assert "ALREADY ACCEPTED WORK" in text
    for demand in m.active_demands():
        assert demand.demand_id not in text
        assert demand.order_index not in [r.get("order_index") for r in rows]


def test_partial_paid_production_is_separate_from_pending_spending_and_blockers():
    m = manager()
    first = m.active_demands()[0]
    first.produced, first.in_flight, first.waiting_for = 2, 3, "resources"
    first.state = DemandState.IN_PRODUCTION
    row = m.production_priority_summary()[0]
    assert (row["remaining"], row["in_production"], row["waiting_to_produce"]) == (6, 3, 3)
    assert row["order_progress"] == "2/8" and row["waiting_for"] == "resources"
    assert first.produced == 2 and first.in_flight == 3


def test_completed_cancelled_failed_rows_drop_and_new_round_appends():
    m = manager()
    old = m.active_demands()
    old[0].mark_completed(1)
    old[1].mark_cancelled(1)
    old[2].mark_failed("lost", 1)
    m.submit_decision([{"action": "build", "target": "factory"}, {"action": "wait"}], game_time=2)
    assert [r["target"] for r in m.production_priority_summary()] == ["stimpack", "marine", "factory"]


def test_unknown_per_order_paid_queue_is_not_guessed_from_phase():
    m = manager()
    m.active_demands()[0].state = DemandState.IN_PRODUCTION
    row = m.production_priority_summary()[0]
    assert row["in_production"] is None and row["waiting_to_produce"] is None
    assert row["remaining"] == 8


def test_environment_context_and_api_share_priority_without_mutating_demand_order():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        env.reset()
        env.task_manager = manager()
        original = copy.deepcopy(env.task_manager.production_priority_summary())
        obs = env._build_observation(env.backend.snapshot())
        assert obs.to_dict()["production_priority"] == original
        assert obs.map_topology["distance_basis"] is None  # Fake has no real terrain.
        text = "\n".join(obs.section_lines())
        assert "[Production Priority]" in text and "[Map Topology]\nunknown" in text
        assert "cmd_" not in text and "demand_id" not in text
        assert env.task_manager.production_priority_summary() == original
        prompt = render_system_prompt()
        for phrase in ["numbers do not imply adjacency", "final objective, not the next pathfinding hop",
                       "each scout-route waypoint", "real cross-round priority order",
                       "Missing distances/links are unknown, not unreachable"]:
            assert phrase in prompt
    finally:
        env.close()
