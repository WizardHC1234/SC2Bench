"""Regressions for native inventory, morph sequencing and experiment resume."""
import asyncio
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
_ensure_runtime_paths()
from sc2.ids.unit_typeid import UnitTypeId as U
from sc2.ids.ability_id import AbilityId as A
from sc2.units import Units
from sc2bench_env.backends.sharpy.races.base import owned_unit_count
from sc2bench_env.backends.sharpy.races.protoss_warp import ActGatewayTrain, ActArchonMerge, _ready_warpgates
from sc2bench_env.backends.sharpy.races.zerg import BenchMorph
from sc2bench_env.backends.sharpy.acts import ActChrono
from sc2bench_env.runtime.observation_builder import compact_production_priority
from sc2bench_env.benchmark import BenchmarkRunner, Evaluator
from sc2bench_env.interface.config import EpisodeConfig
from tests.unit.test_evaluation_capability import _suite
from tests.helpers.drive import scripted_advance
from agents.run_suite import ensure_run_identity, remaining_case_suite, episode_counts_for_quota


def unit(tag, kind):
    u = MagicMock(tag=tag, type_id=kind, is_ready=True, is_hallucination=False)
    u.orders, u.passengers = [], []
    return u


def inventory(*units, structures=()):
    ai = NS(bench_group0_tags={u.tag for u in units}, bench_combat_tags=set(),
            unit_tags_received_action=set(), bench_morph_tags=set())
    ai.units, ai.structures = Units(list(units), ai), Units(list(structures), ai)
    return ai


@pytest.mark.parametrize("kind,carrier_kind", [(U.MARINE, U.BUNKER), (U.ZEALOT, U.WARPPRISM), (U.BANELING, U.OVERLORDTRANSPORT)])
def test_native_inventory_deduplicates_map_and_cargo(kind, carrier_kind):
    passenger, carrier = unit(1, kind), unit(2, carrier_kind)
    carrier.passengers = [passenger, passenger]
    ai = inventory(carrier, passenger)
    assert owned_unit_count(ai, kind) == 1
    ai.units = Units([carrier], ai)
    assert owned_unit_count(ai, kind) == 1


def test_gateway_does_not_replace_a_loaded_unit():
    passenger, prism = unit(1, U.ZEALOT), unit(2, U.WARPPRISM)
    prism.passengers = [passenger]
    act = ActGatewayTrain(U.ZEALOT, 1)
    act.ai = inventory(prism)
    act.ai.already_pending = lambda _: 0
    assert asyncio.run(act.execute()) is True
    prism.train.assert_not_called()


def test_zerg_completed_morph_does_not_hold_an_extra_source():
    source, result = unit(1, U.ZERGLING), unit(2, U.BANELING)
    act = BenchMorph(U.ZERGLING, A.MORPHZERGLINGTOBANELING_BANELING, U.BANELING, U.BANELINGCOCOON, 1)
    act.ai = inventory(source, result)
    act.reserve_pending_sources()
    assert not act.ai.bench_morph_tags
    assert asyncio.run(act.execute()) is True
    source.assert_not_called()


def test_zerg_prefix_reservation_stops_at_required_quantity():
    act = BenchMorph(U.ZERGLING, A.MORPHZERGLINGTOBANELING_BANELING, U.BANELING, U.BANELINGCOCOON, 1)
    act.ai = inventory(unit(1, U.ZERGLING), unit(2, U.ZERGLING))
    act.ai.bench_morph_tags = {1}
    act.reserve_pending_sources()
    assert act.ai.bench_morph_tags == {1}


def test_multiple_archons_continue_after_the_first_pair_is_consumed():
    templar = [unit(i, U.HIGHTEMPLAR) for i in range(1, 5)]
    act = ActArchonMerge(2)
    act.ai = inventory(*templar)
    transport = AsyncMock(return_value=NS(action=NS(result=[1])))
    act.ai.client = NS(_execute=transport)
    assert asyncio.run(act.execute()) is False
    command = transport.call_args.kwargs["action"].actions[0].action_raw.unit_command
    assert list(command.unit_tags) == [1, 2] and not command.HasField("target_unit_tag")
    act.ai.units = Units([*templar[2:], unit(5, U.ARCHON)], act.ai)
    assert asyncio.run(act.execute()) is False
    assert act.failure_reason is None
    command = transport.call_args.kwargs["action"].actions[0].action_raw.unit_command
    assert list(command.unit_tags) == [3, 4]
    act.ai.units = Units([unit(5, U.ARCHON), unit(6, U.ARCHON)], act.ai)
    assert asyncio.run(act.execute()) is True


def test_failed_ability_query_does_not_invent_a_ready_warpgate():
    ai = NS(get_available_abilities=AsyncMock(side_effect=RuntimeError("query failed")))
    assert asyncio.run(_ready_warpgates(ai, [unit(1, U.WARPGATE)])) is None


def test_warpgate_readiness_is_not_confused_with_affordability():
    gate = unit(1, U.WARPGATE)
    query = AsyncMock(return_value=[[A.WARPGATETRAIN_ZEALOT]])
    assert asyncio.run(_ready_warpgates(NS(get_available_abilities=query), [gate])) == [gate]
    query.assert_awaited_once_with([gate], ignore_resource_requirements=False)


def test_rejected_archon_command_does_not_lock_sources_or_claim_output():
    act = ActArchonMerge(1)
    act.ai = inventory(unit(1, U.HIGHTEMPLAR), unit(2, U.HIGHTEMPLAR))
    act.ai.client = NS(_execute=AsyncMock(return_value=NS(action=NS(result=[3]))))
    assert asyncio.run(act.execute()) is True
    assert act.failure_reason == "merge_command_rejected" and not act._locked
    assert act.ai.bench_group0_tags == {1, 2}


def test_archon_waiting_sources_are_reserved_from_later_combat():
    act = ActArchonMerge(1)
    act.ai = inventory(unit(1, U.HIGHTEMPLAR), unit(2, U.HIGHTEMPLAR), unit(3, U.HIGHTEMPLAR))
    act.reserve_pending_sources()
    assert act.ai.bench_morph_tags == {1, 2}


def test_chrono_does_not_spend_on_an_idle_building():
    act = ActChrono()
    act.ai = inventory(structures=[unit(1, U.GATEWAY)])
    assert act._target() is None
    act.ai.structures[0].orders = [object()]
    assert act._target().tag == 1


def test_priority_compression_never_moves_requests_across_other_work():
    rows = [dict(action=a, target=t, remaining=1) for a,t in [
        ("train", "marine"), ("train", "marine"), ("build", "barracks"), ("train", "marine")]]
    compact = compact_production_priority(rows)
    assert [(r["target"], r["remaining"]) for r in compact] == [("marine", 2), ("barracks", 1), ("marine", 1)]
    assert len(rows) == 4


def test_resume_keeps_missing_seeds_and_counts_short_natural_endings(tmp_path):
    suite = _suite(game_seeds=[11, 12], purpose="workflow_pilot")
    runner = BenchmarkRunner(backend_factory=lambda: "fake", record_dir=tmp_path)
    batch = runner.run([EpisodeConfig(seed=11, game_time_limit_seconds=1)],
                       lambda: lambda request: scripted_advance(request, seconds=1))
    assert episode_counts_for_quota(Path(batch["episodes"][0]["record_directory"]))
    data = suite.to_dict()
    remaining = remaining_case_suite(data, data["cases"][0], tmp_path)
    assert remaining.to_dict()["game_seeds"] == [12]
    assert remaining.to_dict()["repetitions"] == 1


def test_resume_rejects_changed_identity_without_touching_old_metadata(tmp_path):
    suite = _suite()
    ensure_run_identity(tmp_path, suite, {"model": "first"}, service_url="http://local")
    original = (tmp_path / "run_identity.json").read_bytes()
    ensure_run_identity(tmp_path, suite, {"model": "first"}, service_url="http://local")
    with pytest.raises(ValueError, match="different experiment"):
        ensure_run_identity(tmp_path, suite, {"model": "second"}, service_url="http://local")
    assert (tmp_path / "run_identity.json").read_bytes() == original
    assert "http://local" not in original.decode()


def test_evaluator_distinguishes_missing_sources_from_known_conflicts():
    row = dict(config={"race": "terran"}, backend="fake", status="completed", outcome="victory",
               platform_prompt_sha256="prompt", versions={"platform_source_sha256": "a"})
    missing = {**row, "versions": {}}
    assert Evaluator.measure([missing])["comparison_status"] == "unknown"
    other = {**row, "versions": {"platform_source_sha256": "b"}}
    metrics = Evaluator.measure([row, other])
    assert metrics["comparison_status"] == "incompatible" and metrics["win_rate"] is None
    race_other = {**row, "config": {"race": "zerg"}, "platform_prompt_sha256": "zerg-prompt"}
    assert Evaluator.measure([row, race_other])["comparison_status"] != "incompatible"


@pytest.mark.parametrize("limit", [0, -1, float("nan"), float("inf"), True])
def test_invalid_game_clock_is_rejected_before_starting_a_game(limit):
    with pytest.raises(ValueError, match="game_time_limit"):
        EpisodeConfig(game_time_limit_seconds=limit)


@pytest.mark.parametrize("race", ["terran", "protoss", "zerg"])
def test_every_dispatchable_catalog_unit_has_a_native_selection_mapping(race):
    from sc2bench_env.backends.sharpy.races import get_adapter
    from sc2bench_env.catalog.registry import targets_for_action
    adapter = get_adapter(race)
    for spec in targets_for_action("train", race=race):
        if spec.dispatchable:
            assert adapter.train_unit_type(spec.name) is not None, spec.name
            assert adapter.combat_forms(spec.name), spec.name


@pytest.mark.parametrize("race,name,kind", [("protoss", "archon", U.ARCHON), ("zerg", "transport_overlord", U.OVERLORDTRANSPORT)])
def test_morphed_units_can_actually_be_selected_for_a_mission(race, name, kind):
    from tests.unit.test_combat_execution import UnitStub, mission
    from sc2bench_env.backends.sharpy.races import get_adapter
    act = mission(own=[UnitStub(1, kind)])
    act.ai.adapter = get_adapter(race)
    act.ai.bench_group0_tags = {1}
    tags, error = act._select_group0_units({name: 1})
    assert tags == [1] and error is None


@pytest.mark.parametrize("method,kind", [("build_terran", U.BARRACKS), ("build_protoss", U.TWILIGHTCOUNCIL), ("build_zerg", U.SPAWNINGPOOL)])
@pytest.mark.parametrize("can_find", [True, False])
def test_cached_illegal_grid_location_is_never_sent_as_a_build(method, kind, can_find):
    from sharpy.plans.acts import GridBuilding
    from sc2.position import Point2
    act = GridBuilding(kind)
    bad, good = Point2((10, 10)), Point2((14, 10))
    act.ai = NS(can_place=AsyncMock(return_value=[False]),
                find_placement=AsyncMock(return_value=good if can_find else None))
    act.clear_worker = MagicMock()
    act.has_build_order = lambda _: False
    worker = MagicMock()
    asyncio.run(getattr(act, method)(worker, 0, bad))
    if can_find:
        worker.build.assert_called_once_with(kind, good)
    else:
        worker.build.assert_not_called()
        assert act.waiting_reason == "no_build_location"
        act.clear_worker.assert_called_once()
