"""Race data separation without changing the Terran public contract."""

from dataclasses import replace

import pytest

from sc2bench_env.catalog import registry as catalog
from sc2bench_env.catalog.models import TargetSpec
from sc2bench_env.catalog.races import get_catalog
from sc2bench_env.catalog.races.terran import TERRAN_TARGETS


def test_existing_exports_refer_to_the_single_race_data_source():
    assert catalog.TargetSpec is TargetSpec
    assert catalog.TERRAN_TARGETS is TERRAN_TARGETS is get_catalog().targets
    assert catalog.catalog_as_dicts() == [spec.to_dict() for spec in TERRAN_TARGETS]
    assert catalog.get_target(" MARINE ") is next(
        spec for spec in TERRAN_TARGETS if spec.name == "marine"
    )


def test_explicit_terran_queries_match_existing_defaults():
    assert catalog.get_target("marine", race="terran") == catalog.get_target("marine")
    assert catalog.targets_for_action("train", race="terran") == catalog.targets_for_action("train")
    assert catalog.known_target_names(race="terran") == catalog.known_target_names()
    for query in (catalog.cost_table, catalog.prerequisite_table, catalog.catalog_as_dicts):
        assert query(race="terran") == query()


@pytest.mark.parametrize("race", ["random"])
def test_registry_does_not_substitute_terran_for_unimplemented_races(race):
    with pytest.raises(ValueError, match="Unsupported own race"):
        get_catalog(race=race)


@pytest.mark.parametrize("query,args", [
    (catalog.get_target, ("marine",)),
    (catalog.targets_for_action, ("train",)),
    (catalog.known_target_names, ()),
    (catalog.cost_table, ()),
    (catalog.prerequisite_table, ()),
    (catalog.catalog_as_dicts, ()),
])
def test_all_metadata_queries_reject_unsupported_race(query, args):
    with pytest.raises(ValueError, match="Unsupported own race"):
        query(*args, race="random")


def test_catalog_validation_accepts_one_pass_iterables():
    catalog.validate_catalog(iter(TERRAN_TARGETS))


def test_catalog_validation_detects_duplicates_in_one_pass_iterables():
    spec = catalog.get_target("supply_depot")
    with pytest.raises(ValueError, match="duplicate catalog target"):
        catalog.validate_catalog(iter((spec, spec)))


def test_catalog_validation_detects_unknown_prerequisites_in_one_pass_iterables():
    spec = replace(catalog.get_target("marine"), prerequisites=("missing",))
    with pytest.raises(ValueError, match="unknown prerequisite"):
        catalog.validate_catalog(iter((spec,)))


def test_queries_cannot_mutate_the_source_catalog():
    costs = catalog.cost_table()
    costs["marine"]["minerals"] = -1
    prerequisites = catalog.prerequisite_table()
    prerequisites["marine"].clear()
    assert catalog.get_target("marine").minerals == 50
    assert catalog.get_target("marine").prerequisites == ("barracks",)
    with pytest.raises(TypeError):
        get_catalog().target_notes["train"] = ()


def test_reference_notes_are_selected_from_the_race_catalog(monkeypatch):
    data = replace(
        get_catalog(), table_legend=("Selected race legend",),
        target_notes={"train": ("Selected race training note",)},
    )
    monkeypatch.setattr(catalog, "get_catalog", lambda *, race="terran": data)
    text = catalog.render_action_catalog(race="terran")
    assert "Selected race legend" in text
    assert "Selected race training note" in text
    assert "Selected race legend" not in catalog.render_decision_guide(race="terran")


def test_action_catalog_does_not_embed_wait_conditions():
    assert not hasattr(catalog, "WAIT_CONDITIONS")
