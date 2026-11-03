"""Protoss and Zerg execution rules that do not need a live client."""

import pytest

from sc2bench_env.backends.sharpy.combat_styles import unit_available_for_background
from sc2bench_env.backends.sharpy.races.protoss import ProtossAdapter
from sc2bench_env.backends.sharpy.races.terran import TerranAdapter
from sc2bench_env.backends.sharpy.races.zerg import ZergAdapter
from sc2bench_env.catalog.registry import get_target, known_target_names
from sc2bench_env.interface.actions import ActionValidationError, parse_decision
from sc2bench_env.runtime.query_tools import query_building_data, query_race_data
from sc2.ids.unit_typeid import UnitTypeId


class _Unit:
    def __init__(self, tag):
        self.tag = tag
        self.is_ready = True
        self.is_hallucination = False
        self.orders = []


class _Ai:
    def __init__(self):
        self.bench_combat_tags = {2}
        self.bench_group0_tags = {1}
        self.bench_bunker_tags = set()
        self.unit_tags_received_action = set()
        self.roles = None


def test_nydus_network_is_not_an_executable_build():
    assert "nydus_network" not in known_target_names("build", race="zerg")
    assert "nydus_network" not in query_race_data("zerg")["buildings"]
    spec = get_target("nydus_network", race="zerg")
    assert spec is not None and spec.executable is False
    with pytest.raises(ActionValidationError):
        parse_decision(
            [{"name": "build", "arguments": {"target": "nydus_network"}}],
            race="zerg",
        )


def test_building_queries_use_the_race_worker():
    assert query_building_data(["pylon"], race="protoss")["results"][0]["builder"] == "probe"
    assert query_building_data(["spawning_pool"], race="zerg")["results"][0]["builder"] == "drone"
    assert query_building_data(["supply_depot"], race="terran")["results"][0]["builder"] == "scv"


def test_home_gather_follows_the_race_adapter():
    assert TerranAdapter().home_production_types() == frozenset({
        UnitTypeId.BARRACKS, UnitTypeId.FACTORY, UnitTypeId.STARPORT,
    })
    assert UnitTypeId.GATEWAY in ProtossAdapter().home_production_types()
    assert UnitTypeId.ROBOTICSFACILITY in ProtossAdapter().home_production_types()
    assert UnitTypeId.STARGATE in ProtossAdapter().home_production_types()
    zerg = ZergAdapter()
    assert zerg.home_production_types() == frozenset()
    assert zerg.home_gather_excluded() == frozenset({"queen", "overlord", "overseer", "transport_overlord"})


def test_background_conversion_leaves_outbound_units():
    ai = _Ai()
    assert unit_available_for_background(_Unit(1), ai, require_group0=True)
    assert not unit_available_for_background(_Unit(2), ai, require_group0=True)
    assert not unit_available_for_background(_Unit(3), ai, require_group0=True)
    assert unit_available_for_background(_Unit(3), ai, require_group0=False)
    assert not unit_available_for_background(_Unit(2), ai, require_group0=False)


def test_warp_gate_research_does_not_install_an_automatic_morph():
    import inspect

    import sc2bench_env.backends.sharpy.races.protoss as protoss

    assert not hasattr(protoss, "PlanWarpGateMorph")
    assert "PlanWarpGateMorph" not in inspect.getsource(ProtossAdapter.create_tactics)


def _linux_snapshot():
    from sc2bench_env.catalog.knowledge import require_snapshot_match

    return require_snapshot_match(
        game_version="4.10.0.75689",
        data_version="B89B5D6FA7CBF6452E721311BFBC6CB2",
        base_build=75689,
    )


def test_versioned_targets_follow_the_bound_snapshot():
    from sc2bench_env.catalog.knowledge import game_data, snapshot_has_ability
    from sc2bench_env.runtime.query_tools import query_unit_data

    windows_gate = get_target("warpgate", race="protoss")
    windows_archon = get_target("archon", race="protoss")
    windows_transport = get_target("transport_overlord", race="zerg")
    assert windows_gate.minerals == 25 and windows_gate.vespene == 25
    assert windows_gate.base_time_seconds == 4
    assert windows_archon.minerals == 0 and windows_archon.vespene == 0 and windows_archon.supply == 0
    assert windows_transport.minerals == 0 and windows_transport.vespene == 0
    assert windows_transport.base_time_seconds == 15
    assert "microbial_shroud" in known_target_names("research", race="zerg")
    assert "interference_matrix" in known_target_names("research", race="terran")
    assert snapshot_has_ability("MorphBuildingGatewayWarpGateFree")

    with game_data(_linux_snapshot()):
        linux_gate = get_target("warpgate", race="protoss")
        linux_transport = get_target("transport_overlord", race="zerg")
        assert linux_gate.minerals == 0 and linux_gate.vespene == 0
        assert linux_gate.base_time_seconds == 7
        assert get_target("archon", race="protoss").supply == 0
        assert linux_transport.minerals == 0 and linux_transport.base_time_seconds == 12
        assert "microbial_shroud" not in known_target_names("research", race="zerg")
        assert "interference_matrix" not in known_target_names("research", race="terran")
        assert not snapshot_has_ability("MorphBuildingGatewayWarpGateFree")
        assert snapshot_has_ability("ArchonWarp")
        assert snapshot_has_ability("MorphToTransportOverlord")
        from sc2bench_env.runtime.observation_builder import available_targets
        from sc2bench_env.interface.actions import parse_game_action
        from sc2bench_env.catalog.registry import get_catalog
        for race in ("terran", "protoss", "zerg"):
            buildings = {s.name: {"completed": 1} for s in get_catalog(race=race).targets
                         if s.action in {"build", "morph_townhall"}}
            available = available_targets(race=race, building=buildings)
            for target in available["research"]:
                parse_game_action({"name": "research", "arguments": {"target": target}}, race=race)
            if race == "terran":
                assert "interference_matrix" not in available["research"]
            if race == "zerg":
                assert "microbial_shroud" not in available["research"]

    overlord = query_unit_data(["overlord"], race="zerg")["results"][0]
    transport = query_unit_data(["transport_overlord"], race="zerg")["results"][0]
    tower = query_building_data(["sensor_tower"], race="terran")["results"][0]
    assert "transport" not in overlord["roles"]
    assert "transport" in transport["roles"]
    assert "detector" not in tower["roles"]
    assert query_building_data(["warpgate"], race="protoss")["results"][0]["builder"] == "gateway"


def test_route_prefers_the_shorter_known_path():
    from sc2bench_env.runtime.query_tools import query_route

    observation = {"map_topology": {"zones": [
        {"zone_id": "a", "corridor_neighbors": [
            {"zone_id": "b", "path_distance": 10},
            {"zone_id": "c", "path_distance": 1},
        ]},
        {"zone_id": "c", "corridor_neighbors": [
            {"zone_id": "a", "path_distance": 1},
            {"zone_id": "b", "path_distance": 1},
        ]},
        {"zone_id": "b", "corridor_neighbors": [
            {"zone_id": "a", "path_distance": 10},
            {"zone_id": "c", "path_distance": 1},
        ]},
    ]}}
    route = query_route(observation, "a", "b")
    assert route["path"] == ["a", "c", "b"]
    assert route["total_distance"] == 2
    assert route["distance_meaning"] == "shortest_known_path"


def test_tool_contract_rejects_empty_names_and_invalid_group_dispatch():
    from sc2bench_env.interface.tools import tool_specs, validate_tool_arguments

    unit_spec = next(spec for spec in tool_specs("zerg") if spec.name == "query_unit_data")
    assert unit_spec.parameters["properties"]["names"]["minItems"] == 1
    assert validate_tool_arguments(unit_spec, {"race": "zerg", "names": []})
    combat = next(spec for spec in tool_specs("protoss") if spec.name == "dispatch_group")
    assert validate_tool_arguments(
        combat, {
            "source": "group_0", "target": "zone_1",
            "units": {"zealot": 1}, "order": "attack",
        },
    ) is None
    with pytest.raises(ActionValidationError, match="group_0"):
        parse_decision(
            [{"name": "attack_group", "arguments": {
                "group": "group_0", "target": "zone_1",
            }}],
            race="protoss",
        )


def test_tool_contract_exposes_only_race_valid_action_options():
    from sc2bench_env.interface.tools import action_tool_names, tool_specs

    by_race = {
        race: {spec.name: spec for spec in tool_specs(race)}
        for race in ("terran", "protoss", "zerg")
    }

    assert "Command Center upgrades use morph_townhall" in by_race["terran"]["build"].description
    assert "Orbital Command or Planetary Fortress" in by_race["terran"]["morph_townhall"].description
    assert "warps" not in by_race["terran"]["train"].description
    assert "hatches" not in by_race["terran"]["train"].description
    assert "warps in or merges" in by_race["protoss"]["train"].description
    assert "hatches or morphs" in by_race["zerg"]["train"].description

    for race in ("terran", "zerg"):
        retreat = by_race[race]["retreat_group"]
        assert "method" not in retreat.parameters["properties"]
    assert by_race["protoss"]["retreat_group"].parameters["properties"]["method"]["enum"] == ["move", "recall"]

    def cancel_actions(race):
        cancel = by_race[race]["cancel"]
        return cancel.parameters["properties"]["target_action"]["enum"]

    assert "morph_townhall" in cancel_actions("terran")
    assert "morph_townhall" not in cancel_actions("protoss")
    assert "morph_townhall" in cancel_actions("zerg")

    common = {
        "build", "train", "research", "cancel", "scout",
        "dispatch_group", "attack_group", "defend_group", "reinforce_group", "merge_groups",
        "retreat_group", "advance",
    }
    for race in by_race:
        assert common <= set(action_tool_names(race))
    assert set(action_tool_names("terran")) - common == {
        "morph_townhall", "scan", "call_mule", "supply_drop",
    }
    assert set(action_tool_names("protoss")) - common == {"chrono_boost"}
    assert set(action_tool_names("zerg")) - common == {
        "morph_townhall", "inject_larva", "spawn_creep_tumor",
    }

    for race in by_race:
        dispatch = by_race[race]["dispatch_group"]
        assert dispatch.parameters["properties"]["units"]["minProperties"] == 1
        for name in ("attack_group", "defend_group"):
            assert "units" not in by_race[race][name].parameters["properties"]


def test_race_ability_observation_exposes_each_public_strategic_option():
    from types import SimpleNamespace
    from sc2bench_env.runtime.observation_builder import build_race_view

    snapshots = {
        "terran": SimpleNamespace(info={
            "scan_ready": 1, "mule_ready": 1, "supply_drop_ready": 1,
            "supply_drop_targets": 2, "orbital_energies": [75],
        }, buildings={"orbital_command": 1}),
        "protoss": SimpleNamespace(info={
            "chrono_ready": 1, "recall_ready": 1, "nexus_energies": [75],
        }, buildings={"nexus": 1}),
        "zerg": SimpleNamespace(info={
            "inject_ready": 1, "inject_targets": 2,
            "spawn_creep_tumor_ready": 3, "queen_energies": [50],
        }, buildings={"hatchery": 2}),
    }
    expected = {
        "terran": {"scan_ready", "mule_ready", "supply_drop_ready", "supply_drop_targets"},
        "protoss": {"chrono_ready", "recall_ready"},
        "zerg": {"inject_ready", "inject_targets", "spawn_creep_tumor_ready"},
    }
    for race, snapshot in snapshots.items():
        abilities = build_race_view(race=race, snapshot=snapshot).abilities
        assert expected[race] <= set(abilities)


def test_special_train_and_build_targets_use_dedicated_acts():
    from sc2bench_env.backends.sharpy.races.protoss_warp import (
        ActArchonMerge, ActGatewayTrain, ActWarpGateConversion,
    )
    from sc2bench_env.runtime.task import Demand

    protoss = ProtossAdapter()
    assert isinstance(
        protoss.create_act(Demand("build", "warpgate"), to_count=1), ActWarpGateConversion,
    )
    assert isinstance(
        protoss.create_act(Demand("train", "archon"), to_count=1), ActArchonMerge,
    )
    assert isinstance(
        protoss.create_act(Demand("train", "zealot"), to_count=1), ActGatewayTrain,
    )
    transport = ZergAdapter().create_act(Demand("train", "transport_overlord"), to_count=1)
    overseer = ZergAdapter().create_act(Demand("train", "overseer"), to_count=1)
    ravager = ZergAdapter().create_act(Demand("train", "ravager"), to_count=1)
    assert transport.require_group0 is False
    assert overseer.require_group0 is False
    assert ravager.require_group0 is True


def test_larva_shortage_is_not_reported_as_a_busy_producer():
    from sc2bench_env.backends.sharpy.races.zerg_execution import execution_blocker

    class _Empty:
        def __iter__(self):
            return iter(())

    class _Ai:
        def units(self, _type):
            return _Empty()

    assert execution_blocker(_Ai(), {"action": "train", "target": "drone"}) == "no_larva"


def test_warpgate_capacity_does_not_invent_a_queue():
    from types import SimpleNamespace

    from sc2bench_env.backends.sharpy.races.protoss_production import read_production_capacity

    ai = SimpleNamespace(
        structures=[],
        bench_warpgate_status={"total": 2, "ready": 1, "cooling": 1},
    )
    row = next(item for item in read_production_capacity(ai, ProtossAdapter()) if item["facility"] == "warpgate")
    assert row["queue_capacity"] is None
    assert row["queued_orders"] is None
    assert row["free_queue_positions"] is None
    assert row["ready_to_warp"] == 1
    assert row["cooling"] == 1


def test_gateway_and_warpgate_capacity_are_counted_separately():
    from types import SimpleNamespace

    from sc2bench_env.backends.sharpy.races.protoss_production import read_production_capacity

    gateway = SimpleNamespace(
        type_id=SimpleNamespace(name="GATEWAY"), tag=1, build_progress=1, orders=[],
    )
    warpgate = SimpleNamespace(
        type_id=SimpleNamespace(name="WARPGATE"), tag=2, build_progress=1, orders=[],
    )
    ai = SimpleNamespace(
        structures=[gateway, warpgate],
        bench_warp_reserve_tags=set(),
        bench_warpgate_status={"total": 1, "ready": 1, "cooling": 0},
    )

    rows = read_production_capacity(ai, ProtossAdapter())
    gateway_row = next(item for item in rows if item["facility"] == "gateway")
    warpgate_row = next(item for item in rows if item["facility"] == "warpgate")

    assert ProtossAdapter().normalize_unit_name("WARPGATE") == "gateway"
    assert gateway_row["ready_grounded"] == 1
    assert warpgate_row["ready_grounded"] == 1
