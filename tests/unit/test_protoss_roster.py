"""Protoss catalog, adapter and observation use Protoss data only."""
import asyncio
from tests.helpers.drive import drive
from types import SimpleNamespace

import pytest

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths

_ensure_runtime_paths()
from sc2.ids.unit_typeid import UnitTypeId
from sc2bench_env import Environment, EpisodeConfig
from sc2bench_env.backends.sharpy.defense_placement import DefensiveGridBuilding
from sc2bench_env.backends.sharpy.races.protoss import (
    ProtossAdapter, ProtossGridBuilding, UNITS, combat_unit_types,
)
from sc2bench_env.backends.sharpy.races.protoss_warp import (
    ActGatewayTrain, ActWarpGateConversion,
)
from sc2bench_env.catalog.registry import get_target, render_system_prompt, targets_for_action
from sc2bench_env.interface.actions import ActionValidationError, parse_decision
from sc2bench_env.runtime.observation_builder import build_race_view
from sc2bench_env.backends.base import BackendSnapshot
from sc2bench_env.interface.tools import action_tool_names

ROSTER = {
    "probe", "zealot", "stalker", "sentry", "adept", "high_templar", "dark_templar",
    "observer", "warp_prism", "immortal", "colossus", "disruptor", "phoenix", "oracle",
    "void_ray", "tempest", "carrier", "mothership",
}


def test_train_roster_matches_catalog_and_not_terran():
    assert set(UNITS) == ROSTER
    assert {spec.name for spec in targets_for_action("train", race="protoss")} == ROSTER | {"archon"}
    assert get_target("marine", race="protoss") is None
    assert get_target("zealot", race="protoss").minerals == 100
    assert get_target("immortal", race="protoss").minerals == 250
    assert get_target("pylon", race="protoss").minerals == 100
    assert get_target("warp_gate", race="protoss").prerequisites == ("cybernetics_core",)
    parse_decision([
        {"name": "combat", "arguments": {
            "style": "attack", "target": "zone_0",
            "units": {name: 1 for name in ROSTER - {"probe"}},
        }},
        {"name": "advance", "arguments": {"seconds": 5}},
    ], race="protoss")


@pytest.mark.parametrize(
    "spec",
    targets_for_action("train", race="protoss")
    + targets_for_action("build", race="protoss")
    + targets_for_action("research", race="protoss"),
    ids=lambda spec: spec.name,
)
def test_every_catalog_target_has_an_act(spec):
    act = ProtossAdapter().create_act(SimpleNamespace(action=spec.action, target=spec.name), to_count=1)
    assert act is not None
    if spec.name in {"photon_cannon", "shield_battery"}:
        assert isinstance(act, DefensiveGridBuilding)
    elif spec.name == "warpgate":
        assert isinstance(act, ActWarpGateConversion)
    elif spec.action == "build" and spec.name not in {"nexus", "assimilator"}:
        assert isinstance(act, ProtossGridBuilding)


def test_warp_gate_and_phasing_share_train_identity():
    adapter = ProtossAdapter()
    assert adapter.normalize_unit_name("WARPGATE") == "gateway"
    assert adapter.normalize_unit_name("OBSERVERSIEGEMODE") == "observer"
    assert adapter.normalize_unit_name("WARPPRISMPHASING") == "warp_prism"
    assert UnitTypeId.WARPGATE in combat_unit_types("gateway")
    assert UnitTypeId.WARPPRISMPHASING in combat_unit_types("warp_prism")


def test_cooling_warpgate_waits_without_switching_back_to_gateway(monkeypatch):
    from sc2bench_env.backends.sharpy.races import protoss_warp

    class UnitCollection(list):
        @property
        def ready(self):
            return self

        @property
        def idle(self):
            return self

        @property
        def amount(self):
            return len(self)

    issued = []

    class Gate:
        tag = 7
        orders = []

        def __call__(self, ability):
            issued.append(ability)

    gate = Gate()

    def structures(unit_type):
        return UnitCollection([gate] if unit_type == UnitTypeId.WARPGATE else [])

    async def none_ready(_ai, _gates):
        return []

    monkeypatch.setattr(protoss_warp, "_ready_warpgates", none_ready)
    act = ActGatewayTrain(UnitTypeId.ZEALOT, 1)
    act.ai = SimpleNamespace(
        units=lambda _unit_type: UnitCollection(),
        structures=structures,
        already_pending=lambda _unit_type: 0,
        unit_tags_received_action=set(),
        can_afford=lambda _:True,
    )
    assert asyncio.run(act.execute()) is False
    assert act.waiting_reason == "warpgate_cooldown"
    assert issued == []


def test_protoss_parser_rejects_terran_actions():
    with pytest.raises(ActionValidationError):
        parse_decision([
            {"name": "train", "arguments": {"target": "marine", "count": 1}},
            {"name": "advance", "arguments": {"seconds": 5}},
        ], race="protoss")
    with pytest.raises(ActionValidationError, match="not available"):
        parse_decision([
            {"name": "scan", "arguments": {"target": "zone_0"}},
            {"name": "advance", "arguments": {"seconds": 5}},
        ], race="protoss")
    with pytest.raises(ActionValidationError, match="not available"):
        parse_decision([
            {"name": "call_mule", "arguments": {}},
            {"name": "advance", "arguments": {"seconds": 5}},
        ], race="protoss")


def test_prompt_and_tools_are_protoss():
    prompt = render_system_prompt(race="protoss")
    assert "Probe" in prompt
    assert "Supply Depots" not in prompt
    assert "Tech Lab" not in prompt
    names = action_tool_names("protoss")
    assert "build" in names and "train" in names and "scout" in names
    assert "scan" not in names and "call_mule" not in names and "morph_townhall" not in names
    assert "chrono_boost" in names
    assert "inject_larva" not in names and "spawn_creep_tumor" not in names
    assert action_tool_names("terran") != names


def test_fake_opening_and_ability_view_are_protoss():
    env = Environment("fake", record_trajectory=False)
    try:
        observation = env.reset(EpisodeConfig(race="protoss", game_time_limit_seconds=30))
        assert observation.game.race == "protoss"
        assert observation.units.get("probe") == 12
        assert observation.buildings.get("nexus") == 1
        assert "marine" not in observation.units
        assert observation.abilities.get("chrono_ready") == 0
        assert "scan_ready" not in observation.abilities
        view = build_race_view(race="protoss", snapshot=BackendSnapshot(
            buildings={"nexus": 1}, info={"chrono_ready": 1, "nexus_energies": [50.0]},
        ))
        assert view.abilities["chrono_ready"] == 1
        assert view.abilities["nexus_energies"] == [50.0]
    finally:
        env.close()


def test_chrono_boost_spends_nexus_energy():
    from sc2bench_env.backends.fake import FakeBackend
    backend = FakeBackend()
    env = Environment(backend, record_trajectory=False)
    try:
        env.reset(EpisodeConfig(race="protoss", game_time_limit_seconds=60))
        backend.buildings["nexus"] = 1
        backend.orbital_energy = 50
        _, feedback, _, _ = drive(env, [
            {"name": "chrono_boost", "arguments": {}},
            {"name": "advance", "arguments": {"seconds": 3}},
        ])
        assert feedback.receipts[0].result == "accepted"
        assert backend.orbital_energy < 25
    finally:
        env.close()


def test_protoss_grid_ignores_natural_wall_and_stays_in_main():
    from sc2.position import Point2
    from sharpy.managers.core.grids import ZoneArea

    wall_pylon = Point2((10, 10))
    main_pylon = Point2((40, 40))
    natural_pylon = Point2((12, 14))
    wall_gate = Point2((11.5, 13.5))
    main_gate = Point2((43.5, 40.5))
    natural_gate = Point2((15.5, 14.5))
    zones = {
        wall_pylon: ZoneArea.OwnNaturalZone,
        main_pylon: ZoneArea.OwnMainZone,
        natural_pylon: ZoneArea.OwnNaturalZone,
        wall_gate: ZoneArea.OwnNaturalZone,
        main_gate: ZoneArea.OwnMainZone,
        natural_gate: ZoneArea.OwnNaturalZone,
    }

    class Cell:
        def __init__(self, zone):
            self.ZoneIndex = zone

    class Grid:
        def __getitem__(self, point):
            return Cell(zones[Point2((point.x, point.y))])

    class Structures:
        def __init__(self, blocked=()):
            self.blocked = {Point2(point) for point in blocked}

        def closer_than(self, _distance, point):
            return point in self.blocked

    class Matrix:
        def __init__(self, centers=()):
            self.centers = [Point2(center) for center in centers]

        def covers(self, point):
            return any(point.distance_to(center) <= 6.5 for center in self.centers)

    solver = SimpleNamespace(
        buildings2x2=[wall_pylon, main_pylon, natural_pylon],
        buildings3x3=[wall_gate, main_gate, natural_gate],
        wall2x2=[wall_pylon],
        wall3x3=[wall_gate],
        grid=Grid(),
    )

    def act_for(unit_type, blocked=(), powered=(), pending=()):
        act = ProtossGridBuilding(unit_type, 1)
        act.building_solver = solver
        act.ai = SimpleNamespace(
            structures=Structures(blocked),
            state=SimpleNamespace(psionic_matrix=Matrix(powered)),
        )
        act.cache = SimpleNamespace(
            own=lambda _type: SimpleNamespace(not_ready=list(pending)),
        )
        return act

    pylon = act_for(UnitTypeId.PYLON)
    assert pylon.position_protoss(0) == main_pylon

    powered_main = act_for(
        UnitTypeId.GATEWAY,
        powered=(main_pylon, wall_pylon),
    )
    assert powered_main.position_protoss(0) == main_gate

    waiting = act_for(UnitTypeId.GATEWAY, powered=(wall_pylon,))
    assert waiting.position_protoss(0) is None

    walking = act_for(UnitTypeId.GATEWAY, pending=(main_pylon,))
    assert walking.position_protoss(0) == main_gate

    full_main = act_for(
        UnitTypeId.GATEWAY,
        blocked=(main_gate,),
        powered=(natural_pylon,),
    )
    assert full_main.position_protoss(0) == natural_gate


def test_pylon_placement_maximizes_new_production_coverage():
    from sc2.position import Point2
    from sharpy.managers.core.grids import ZoneArea

    weak = Point2((10, 10))
    strong = Point2((20, 20))
    natural = Point2((40, 40))
    slots = [Point2((22, 20)), Point2((20, 24)), Point2((12, 10)), Point2((42, 40))]
    zones = {weak: ZoneArea.OwnMainZone, strong: ZoneArea.OwnMainZone,
             natural: ZoneArea.OwnNaturalZone,
             slots[0]: ZoneArea.OwnMainZone, slots[1]: ZoneArea.OwnMainZone,
             slots[2]: ZoneArea.OwnMainZone, slots[3]: ZoneArea.OwnNaturalZone}

    class Grid:
        def __getitem__(self, point):
            return SimpleNamespace(ZoneIndex=zones[Point2((point.x, point.y))])

    class Structures:
        def closer_than(self, _distance, _point):
            return False

    class Matrix:
        def covers(self, _point):
            return False

    act = ProtossGridBuilding(UnitTypeId.PYLON, 1)
    act.building_solver = SimpleNamespace(
        buildings2x2=[weak, strong, natural], buildings3x3=slots,
        wall2x2=[], wall3x3=[], grid=Grid(),
    )
    act.ai = SimpleNamespace(
        structures=Structures(), state=SimpleNamespace(psionic_matrix=Matrix()),
    )
    act.cache = SimpleNamespace(own=lambda _type: SimpleNamespace(not_ready=[]))

    # strong powers two new main production slots; weak powers one.
    assert act.position_protoss(0) == strong

    # Once a pending Pylon will cover the main slots, expand future coverage.
    act.cache = SimpleNamespace(own=lambda _type: SimpleNamespace(not_ready=[weak, strong]))
    assert act.position_protoss(0) == natural
