"""Terran adapter smoke tests (require sharpy import)."""

from __future__ import annotations

import pytest

pytest.importorskip("sc2pathlib")
pytest.importorskip("sharpy")

from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sharpy.plans.acts import ActUnit, Expand, GridBuilding, Tech

from sc2bench_env.backends.sharpy.acts import ActCallMule, ActMorphTownhall, ActScanZone, ActScoutRoute
from sc2bench_env.backends.sharpy.races.terran import TerranAdapter
from sc2bench_env.interface.actions import GameAction
from sc2bench_env.runtime.task import Demand


def test_terran_build_train_and_expand_via_command_center() -> None:
    adapter = TerranAdapter()
    build = Demand.from_action(GameAction("build", "barracks", 1), order_index=0, game_time=0.0)
    train = Demand.from_action(GameAction("train", "marine", 8), order_index=1, game_time=0.0)
    expand = Demand.from_action(
        GameAction("build", "command_center", 1), order_index=2, game_time=0.0
    )
    assert isinstance(adapter.create_act(build, to_count=2), GridBuilding)
    assert isinstance(adapter.create_act(train, to_count=8), ActUnit)
    assert isinstance(adapter.create_act(expand, to_count=2), Expand)


def test_terran_research_upgrade_scan_mule_scout() -> None:
    adapter = TerranAdapter()
    research = Demand.from_action(
        GameAction("research", "stimpack", 1), order_index=0, game_time=0.0
    )
    upgrade = Demand.from_action(
        GameAction("upgrade", target="cc_0", to="orbital_command"),
        order_index=1,
        game_time=0.0,
    )
    scan = Demand.from_action(GameAction("scan", "zone_2", 1), order_index=2, game_time=0.0)
    mule = Demand.from_action(GameAction("call_mule"), order_index=3, game_time=0.0)
    scout = Demand.from_action(
        GameAction("scout", route=("zone_0", "zone_1")),
        order_index=4,
        game_time=0.0,
    )
    research_act = adapter.create_act(research, to_count=1)
    upgrade_act = adapter.create_act(upgrade, to_count=1)
    scan_act = adapter.create_act(scan, to_count=1)
    mule_act = adapter.create_act(mule, to_count=1)
    scout_act = adapter.create_act(scout, to_count=1)
    assert isinstance(research_act, Tech)
    assert research_act.upgrade_type == UpgradeId.STIMPACK
    assert isinstance(upgrade_act, ActMorphTownhall)
    assert upgrade_act.structure_id == "cc_0"
    assert upgrade_act.to == "orbital_command"
    assert isinstance(scan_act, ActScanZone)
    assert isinstance(mule_act, ActCallMule)
    assert isinstance(scout_act, ActScoutRoute)
    assert list(scout_act.route) == ["zone_0", "zone_1"]
    assert adapter.normalize_unit_name("ORBITALCOMMAND") == "orbital_command"
    assert adapter.normalize_unit_name("SUPPLYDEPOTLOWERED") == "supply_depot"
    assert adapter.normalize_unit_name("FACTORYTECHLAB") == "factory_techlab"


def test_terran_tactics_do_not_include_auto_depot() -> None:
    from sharpy.plans.acts.terran import AutoDepot, MorphOrbitals

    adapter = TerranAdapter()
    tactics = adapter.create_tactics()
    orders = list(tactics.orders)
    assert orders, "expected always-on tactics acts"
    assert not any(isinstance(item, AutoDepot) for item in orders)
    assert not any(isinstance(item, MorphOrbitals) for item in orders)
    # Also reject nested under Step(...).
    nested = []
    for item in orders:
        child = getattr(item, "action", None) or getattr(item, "act", None)
        if child is not None:
            nested.append(child)
    assert not any(isinstance(item, (AutoDepot, MorphOrbitals)) for item in nested)


def test_terran_first_wave_army_units() -> None:
    adapter = TerranAdapter()
    for name, unit_type in (
        ("marauder", UnitTypeId.MARAUDER),
        ("siege_tank", UnitTypeId.SIEGETANK),
        ("medivac", UnitTypeId.MEDIVAC),
        ("banshee", UnitTypeId.BANSHEE),
    ):
        demand = Demand.from_action(
            GameAction("train", name, 2), order_index=0, game_time=0.0
        )
        act = adapter.create_act(demand, to_count=2)
        assert isinstance(act, ActUnit)
        assert act.unit_type == unit_type
    assert adapter.normalize_unit_name("SIEGETANKSIEGED") == "siege_tank"
    assert adapter.normalize_unit_name("MARAUDER") == "marauder"
    assert adapter.normalize_unit_name("MEDIVAC") == "medivac"
    assert adapter.normalize_unit_name("BANSHEE") == "banshee"


def test_adapter_maps_follow_catalog() -> None:
    from sc2bench_env.backends.sharpy.races import terran as terran_mod
    from sc2bench_env.interface.action_catalog import get_target, targets_for_action

    for name in terran_mod.BUILDINGS:
        spec = get_target(name)
        assert spec is not None and spec.kind == "building"
    for name in terran_mod.ADDONS:
        spec = get_target(name)
        assert spec is not None and spec.kind == "addon"
    for name in terran_mod.UNITS:
        spec = get_target(name)
        assert spec is not None and spec.kind == "unit"
    for name in terran_mod.RESEARCH:
        spec = get_target(name)
        assert spec is not None and spec.kind == "research"

    # Every catalog train/research target must have an ID bridge entry.
    for spec in targets_for_action("train"):
        assert spec.name in terran_mod.UNITS
    for spec in targets_for_action("research"):
        assert spec.name in terran_mod.RESEARCH
    for spec in targets_for_action("build"):
        if spec.kind == "addon":
            assert spec.name in terran_mod.ADDONS
        elif spec.name == "command_center":
            continue
        elif spec.kind == "building":
            assert spec.name in terran_mod.BUILDINGS or spec.name == "refinery"
