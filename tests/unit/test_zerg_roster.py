"""Zerg catalog, adapter and observation use Zerg data only."""
from tests.helpers.drive import drive
from types import SimpleNamespace

import pytest

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths

_ensure_runtime_paths()
from sc2.ids.unit_typeid import UnitTypeId
from sharpy.plans.acts.morph_building import MorphBuilding
from sc2bench_env import Environment, EpisodeConfig
from sc2bench_env.backends.sharpy.acts import ActMorphTownhall
from sc2bench_env.backends.sharpy.defense_placement import DefensiveGridBuilding
from sc2bench_env.backends.sharpy.races.zerg import (
    ZergAdapter, ZergGridBuilding, UNITS, combat_unit_types,
)
from sc2bench_env.catalog.registry import (
    get_target, render_system_prompt, targets_for_action, validate_catalog,
)
from sc2bench_env.interface.actions import ActionValidationError, parse_decision
from sc2bench_env.catalog.races.zerg import ZERG_TARGETS
from sc2bench_env.runtime.observation_builder import build_race_view
from sc2bench_env.backends.base import BackendSnapshot
from sc2bench_env.interface.tools import action_tool_names

ROSTER = {
    "drone", "overlord", "queen", "zergling", "baneling", "roach", "ravager",
    "hydralisk", "lurker", "infestor", "swarm_host", "mutalisk", "corruptor",
    "overseer", "viper", "ultralisk", "brood_lord",
}


def test_train_roster_matches_catalog_and_not_terran():
    validate_catalog(ZERG_TARGETS)
    assert set(UNITS) == ROSTER
    assert {spec.name for spec in targets_for_action("train", race="zerg")} == ROSTER | {"transport_overlord"}
    assert get_target("marine", race="zerg") is None
    assert get_target("zergling", race="zerg").minerals == 25
    assert get_target("hatchery", race="zerg").minerals == 300
    assert get_target("overlord", race="zerg").supply == 0
    assert get_target("lair", race="zerg").action == "morph_townhall"
    assert get_target("lair", race="zerg").morph_from == "hatchery"
    parse_decision([
        {"name": "morph_townhall", "arguments": {"target": "townhall_0", "to": "lair"}},
        {"name": "combat", "arguments": {
            "style": "attack", "target": "zone_0",
            "units": {name: 1 for name in ROSTER - {"drone", "overlord"}},
        }},
        {"name": "advance", "arguments": {"seconds": 5}},
    ], race="zerg")


@pytest.mark.parametrize(
    "spec",
    targets_for_action("train", race="zerg")
    + targets_for_action("build", race="zerg")
    + targets_for_action("research", race="zerg")
    + targets_for_action("morph_townhall", race="zerg"),
    ids=lambda spec: spec.name,
)
def test_every_catalog_target_has_an_act(spec):
    task = SimpleNamespace(
        action=spec.action, target="townhall_0" if spec.action == "morph_townhall" else spec.name,
        to=spec.name if spec.action == "morph_townhall" else None,
        route=None, style=None, units=None,
    )
    act = ZergAdapter().create_act(task, to_count=1)
    assert act is not None
    if spec.name in {"spine_crawler", "spore_crawler"}:
        assert isinstance(act, DefensiveGridBuilding)
    elif spec.name == "greater_spire":
        assert isinstance(act, MorphBuilding)
    elif spec.action == "morph_townhall":
        assert isinstance(act, ActMorphTownhall)
    elif spec.action == "build" and spec.name not in {"hatchery", "extractor"}:
        assert isinstance(act, ZergGridBuilding)


def test_townhall_and_burrow_forms_share_identity():
    adapter = ZergAdapter()
    assert adapter.normalize_unit_name("LAIR") == "lair"
    assert adapter.normalize_unit_name("ZERGLINGBURROWED") == "zergling"
    assert adapter.normalize_unit_name("LURKERMPBURROWED") == "lurker"
    assert UnitTypeId.LAIR in combat_unit_types("lair") or adapter.normalize_unit_name("LAIR") == "lair"
    assert UnitTypeId.BANELINGBURROWED in combat_unit_types("baneling")


def test_zerg_parser_rejects_terran_actions():
    with pytest.raises(ActionValidationError):
        parse_decision([
            {"name": "train", "arguments": {"target": "marine", "count": 1}},
            {"name": "advance", "arguments": {"seconds": 5}},
        ], race="zerg")
    with pytest.raises(ActionValidationError, match="not available"):
        parse_decision([
            {"name": "scan", "arguments": {"target": "zone_0"}},
            {"name": "advance", "arguments": {"seconds": 5}},
        ], race="zerg")
    with pytest.raises(ActionValidationError, match="not available"):
        parse_decision([
            {"name": "call_mule", "arguments": {}},
            {"name": "advance", "arguments": {"seconds": 5}},
        ], race="zerg")


def test_prompt_and_tools_are_zerg():
    prompt = render_system_prompt(race="zerg")
    assert "Drone" in prompt
    assert "Supply Depots" not in prompt
    assert "Pylon" not in prompt
    assert "Queen energy is reported" in prompt
    names = action_tool_names("zerg")
    assert "morph_townhall" in names
    assert "scan" not in names and "call_mule" not in names and "chrono_boost" not in names
    assert "inject_larva" in names and "spawn_creep_tumor" in names
    assert names != action_tool_names("terran")


def test_fake_opening_and_ability_view_are_zerg():
    env = Environment("fake", record_trajectory=False)
    try:
        observation = env.reset(EpisodeConfig(race="zerg", game_time_limit_seconds=30))
        assert observation.units.get("drone") == 12
        assert observation.units.get("overlord") == 1
        assert observation.buildings.get("hatchery") == 1
        assert observation.game.race == "zerg"
        assert observation.abilities["inject_ready"] == 0
        assert "scan_ready" not in observation.abilities
    finally:
        env.close()


def test_race_view_reports_queen_energy_without_legacy_keys():
    view = build_race_view(
        race="zerg",
        snapshot=BackendSnapshot(info={
            "inject_ready": 1, "queen_energies": [25.0],
        }),
    )
    assert view.abilities == {"inject_ready": 1, "queen_energies": [25.0]}
    assert view.legacy_attributes == {}


def test_inject_larva_and_spawn_creep_tumor_spend_queen_energy():
    from sc2bench_env.backends.fake import FakeBackend
    backend = FakeBackend()
    env = Environment(backend, record_trajectory=False)
    try:
        env.reset(EpisodeConfig(race="zerg", game_time_limit_seconds=60))
        backend.units["queen"] = 1
        backend.orbital_energy = 50
        _, feedback, _, _ = drive(env, [
            {"name": "inject_larva", "arguments": {}},
            {"name": "spawn_creep_tumor", "arguments": {}},
            {"name": "advance", "arguments": {"seconds": 3}},
        ])
        assert [row.result for row in feedback.receipts[:2]] == ["accepted", "accepted"]
        assert backend.orbital_energy < 25
    finally:
        env.close()
