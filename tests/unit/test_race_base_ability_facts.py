"""Base identity and energy observation boundaries; no automatic spending."""

import hashlib
import json
from dataclasses import asdict
from types import SimpleNamespace as NS

import pytest

from sc2bench_env.backends.sharpy.races.base import RaceAdapter
from sc2bench_env.backends.sharpy.races.terran import TerranAdapter
from sc2bench_env.backends.sharpy.state_reader import read_snapshot
from sc2bench_env.backends.sharpy.structures import StructureRegistry
from tests.unit.test_platform_information import building
from tests.unit.test_race_production_orders import Units


def facts_fixture():
    orbitals = [building("ORBITALCOMMAND", 2), building("ORBITALCOMMAND", 3),
                building("ORBITALCOMMAND", 4, ready=False), building("ORBITALCOMMANDFLYING", 5, flying=True)]
    for unit, energy in zip(orbitals, [49.999, 50, 200, 100]):
        unit.energy = energy
    bases = [building("COMMANDCENTER", 1), *orbitals,
             building("PLANETARYFORTRESS", 6), building("COMMANDCENTER", 7, ready=False)]
    return NS(structures=Units(bases), units=Units(), gas_buildings=Units(),
              townhalls=Units(bases), state=NS(upgrades=[]))


def test_existing_terran_base_and_energy_snapshot():
    snapshot, _train, build, _research = read_snapshot(facts_fixture(), TerranAdapter())
    assert snapshot.info["base_count"] == 7
    assert build == {"orbital_command": 1, "base": 2, "command_center": 1}
    assert snapshot.info["orbital_count"] == 3
    assert snapshot.info["orbital_energies"] == [50.0, 50.0, 100.0]
    assert snapshot.info["scan_ready"] == snapshot.info["mule_ready"] == 2
    assert len(snapshot.info["structures"]) == 5


def test_energy_scene_matches_before_refactor_snapshot():
    snapshot = read_snapshot(facts_fixture(), TerranAdapter())[0]
    digest = hashlib.sha256(json.dumps(asdict(snapshot), sort_keys=True).encode()).hexdigest()
    assert digest == "d0d9a0174d4878f8f7ca1b47c60873ea60eed31dad1904d039bb1a9a6e71038d"


def test_base_hook_publishes_no_unsupported_ability_fields():
    assert RaceAdapter.townhall_targets is None
    assert RaceAdapter.read_ability_facts(NS(), object(), {}) == {}
    with pytest.raises(NotImplementedError, match="ready-target counting"):
        RaceAdapter.count_ready(NS(), object(), "barracks")


def test_terran_fallback_base_count_uses_adapter_targets():
    ai = facts_fixture()
    del ai.townhalls
    snapshot = read_snapshot(ai, TerranAdapter())[0]
    assert snapshot.info["base_count"] == 5  # ready observed variants only


def test_unknown_base_types_do_not_fall_back_to_terran():
    ai = facts_fixture()
    del ai.townhalls
    adapter = NS(normalize_unit_name=TerranAdapter().normalize_unit_name)
    snapshot, _train, build, _research = read_snapshot(ai, adapter)
    assert snapshot.info["base_count"] is None
    assert snapshot.info["structures"] == []
    assert "base" not in build
    assert "orbital_command" in snapshot.buildings  # raw facts still preserved
    assert not {"orbital_count", "orbital_energies", "scan_ready", "mule_ready"} & snapshot.info.keys()


def test_custom_adapter_base_identity_and_ability_facts_are_delegated():
    ai = NS(structures=Units([building("NEXUS", 1), building("NEXUS", 2, ready=False)]),
            units=Units(), gas_buildings=Units())
    calls = []
    def abilities(state, buildings):
        calls.append((state, dict(buildings)))
        return {"test_energy": None}
    adapter = NS(normalize_unit_name=lambda name: name.lower(), townhall_targets=("nexus",),
                 read_ability_facts=abilities)
    snapshot, _train, build, _research = read_snapshot(ai, adapter)
    assert snapshot.info["base_count"] == 1
    assert build == {"nexus": 1, "base": 1}
    assert snapshot.info["structures"] == [{"id": "cc_0", "type": "nexus"}]
    assert snapshot.info["test_energy"] is None
    assert calls == [(ai, {"nexus": 1})]


def test_townhall_object_id_survives_morph_and_is_not_reused_after_death():
    registry = StructureRegistry()
    cc = building("COMMANDCENTER", 1)
    ai = NS(structures=Units([cc]), units=Units(), townhalls=Units([cc]), gas_buildings=Units())
    adapter = TerranAdapter()
    assert read_snapshot(ai, adapter, structure_registry=registry)[0].info["structures"] == [{"id": "cc_0", "type": "command_center"}]
    cc.type_id.name = "ORBITALCOMMAND"
    assert read_snapshot(ai, adapter, structure_registry=registry)[0].info["structures"] == [{"id": "cc_0", "type": "orbital_command"}]
    ai.structures = ai.townhalls = Units()
    read_snapshot(ai, adapter, structure_registry=registry)
    assert registry.tag_for_id("cc_0") is None
    ai.structures = ai.townhalls = Units([building("COMMANDCENTER", 9)])
    assert read_snapshot(ai, adapter, structure_registry=registry)[0].info["structures"] == [{"id": "cc_1", "type": "command_center"}]


def test_ability_hook_errors_are_not_masked():
    adapter = TerranAdapter()
    def broken(*args):
        raise RuntimeError("controlled ability observation error")
    adapter.read_ability_facts = broken
    with pytest.raises(RuntimeError, match="controlled ability observation error"):
        read_snapshot(facts_fixture(), adapter)
