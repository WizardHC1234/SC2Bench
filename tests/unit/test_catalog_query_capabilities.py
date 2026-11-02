"""Executable support is owned by the catalog, not stale observation metadata."""
import pytest

from sc2bench_env.catalog.registry import get_catalog
from sc2bench_env.runtime.query_tools import query_unit_data, query_building_data


@pytest.mark.parametrize("race", ["terran", "protoss", "zerg"])
def test_unit_and_building_queries_agree_with_the_executable_catalog(race):
    for spec in get_catalog(race=race).targets:
        if spec.action not in {"train", "build", "morph_townhall"}:
            continue
        query = query_unit_data if spec.action == "train" else query_building_data
        row = query([spec.name], race=race)["results"][0]
        assert row["availability"] == ("controllable" if spec.executable else "knowledge_only"), spec.name


@pytest.mark.parametrize("race,name", [("terran", "mule"), ("protoss", "interceptor"), ("zerg", "changeling")])
def test_observed_only_entities_do_not_become_direct_train_targets(race, name):
    assert query_unit_data([name], race=race)["results"][0]["availability"] == "observable_only"
