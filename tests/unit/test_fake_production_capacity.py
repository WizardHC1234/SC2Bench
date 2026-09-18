"""Shared producer lifetime, attachment and queue-priority contract tests."""
import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.action_catalog import targets_for_action, get_target
from sc2bench_env.interface.config import EpisodeConfig
from tests.helpers.obs_invariants import assert_obs_consistent


def wait(seconds=1):
    return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}


def advance(env, seconds):
    # Long production still runs across decisions; no step bypasses the cap.
    while seconds > 0:
        chunk = min(60, seconds)
        result = env.step([wait(chunk)])
        seconds -= chunk
    return result


def train(target, count=1):
    return {"action": "train", "target": target, "count": count}


def research(target):
    return {"action": "research", "target": target}


@pytest.fixture
def game():
    backend = FakeBackend(mineral_income_per_second=0, vespene_income_per_second=0)
    env = Environment(backend, record_trajectory=False)
    env.reset(EpisodeConfig(decision_interval_seconds=1, game_time_limit_seconds=600))
    backend.minerals = backend.vespene = 10000
    backend.supply_cap = 200
    backend.buildings.update(supply_depot=1)
    try:
        yield env, backend
    finally:
        env.close()


@pytest.mark.parametrize("producer,first,second", [
    ("barracks", "marine", "reaper"),
    ("factory", "hellion", "widow_mine"),
    ("starport", "medivac", "liberator"),
])
def test_different_targets_share_one_parent(game, producer, first, second):
    env, backend = game
    backend.buildings[producer] = 1
    obs, _, _, _ = env.step([train(first), train(second), wait()])
    assert obs.training[first]["in_production"] == 1
    assert obs.training[second]["in_production"] == 0
    assert obs.training[second]["waiting_for"] == "production_capacity"
    assert backend.minerals == 10000 - get_target(first).minerals
    assert_obs_consistent(obs)
    obs, _, _, _ = env.step([wait(get_target(first).base_time_seconds + 1)])
    assert obs.units[first] == 1
    assert obs.training[second]["in_production"] == 1


def test_cross_round_submissions_keep_paid_slot_and_original_priority(game):
    env, backend = game
    backend.buildings["barracks"] = 1
    env.step([train("marine", 2), wait()])
    obs, _, _, _ = env.step([train("reaper"), wait()])
    assert obs.training["reaper"]["in_production"] == 0
    obs, _, _, _ = env.step([wait(18)])
    assert obs.units["marine"] == 1
    assert obs.training["marine"]["in_production"] == 1
    assert obs.training["reaper"]["in_production"] == 0


def test_reactor_channels_are_shared_across_targets(game):
    env, backend = game
    backend.buildings.update(barracks=1, barracks_reactor=1)
    obs, _, _, _ = env.step([train("marine"), train("reaper"), train("marine"), wait()])
    assert obs.training["marine"]["in_production"] == 1
    assert obs.training["reaper"]["in_production"] == 1
    assert obs.training["marine"]["waiting_to_produce"] == 1


def test_lab_and_reactor_have_separate_eligible_channels(game):
    env, backend = game
    backend.buildings.update(barracks=2, barracks_reactor=1, barracks_techlab=1)
    obs, _, _, _ = env.step([train("marine", 2), train("marauder", 2), wait()])
    assert obs.training["marine"]["in_production"] == 2
    assert obs.training["marauder"]["in_production"] == 1
    assert obs.training["marauder"]["waiting_to_produce"] == 1


def test_free_reactor_channel_cannot_replace_busy_lab_channel(game):
    env, backend = game
    backend.buildings.update(barracks=2, barracks_reactor=1, barracks_techlab=1, ghost_academy=1)
    env.step([train("ghost"), train("marine"), train("reaper"), wait()])
    obs, _, _, _ = env.step([train("marauder"), wait(19)])
    assert obs.units["marine"] == 1
    assert obs.training["ghost"]["in_production"] == 1
    assert obs.training["marauder"]["in_production"] == 0
    obs, _, _, _ = env.step([wait(11)])
    assert obs.units["ghost"] == 1
    assert obs.training["marauder"]["in_production"] == 1


def test_two_parents_allow_two_different_targets(game):
    env, backend = game
    backend.buildings["barracks"] = 2
    obs, _, _, _ = env.step([train("marine"), train("reaper"), train("marine"), wait()])
    assert obs.training["marine"]["in_production"] == 1
    assert obs.training["reaper"]["in_production"] == 1


def test_research_retains_slot_after_action_completes(game):
    env, backend = game
    backend.buildings["engineering_bay"] = 1
    obs, _, _, _ = env.step([research("infantry_weapons_1"), research("infantry_armor_1"), wait()])
    assert "infantry_weapons_1" in backend.in_progress_research
    assert "infantry_armor_1" not in backend.in_progress_research
    assert obs.research["infantry_armor_1"] == "waiting_for:production_capacity"
    # Next submit removes the completed high-level research action, not the
    # still-running game research or its occupied Engineering Bay.
    env.step([wait(5)])
    assert backend.in_progress_research == {"infantry_weapons_1"}
    advance(env, 110)
    assert "infantry_weapons_1" in backend.upgrades
    assert backend.in_progress_research == {"infantry_armor_1"}


def test_two_research_buildings_can_research_in_parallel(game):
    env, backend = game
    backend.buildings["engineering_bay"] = 2
    env.step([research("infantry_weapons_1"), research("infantry_armor_1"), wait()])
    assert backend.in_progress_research == {"infantry_weapons_1", "infantry_armor_1"}


def test_lab_research_runs_alongside_parent_training(game):
    env, backend = game
    backend.buildings.update(barracks=1, barracks_techlab=1)
    obs, _, _, _ = env.step([research("stimpack"), research("combat_shield"), train("marine"), wait()])
    assert backend.in_progress_research == {"stimpack"}
    assert obs.training["marine"]["in_production"] == 1
    assert obs.research["combat_shield"] == "waiting_for:production_capacity"


def test_orphan_lab_has_no_research_capacity(game):
    env, backend = game
    backend.buildings["barracks_techlab"] = 1
    env.step([research("stimpack"), wait()])
    assert backend.in_progress_research == set()
    assert backend.minerals == 10000


def test_orphan_lab_does_not_soft_reserve_resources_from_legal_work(game):
    env, backend = game
    backend.buildings.update(barracks_techlab=1, factory=1)
    backend.minerals, backend.vespene = 75, 25
    obs, _, _, _ = env.step([research("stimpack"), train("widow_mine"), wait()])
    assert obs.research["stimpack"] == "waiting_for:prerequisite:barracks_techlab"
    assert obs.training["widow_mine"]["in_production"] == 1


def test_existing_reactor_identity_survives_additional_parent(game):
    env, backend = game
    backend.buildings.update(barracks=1, barracks_reactor=1)
    env.step([train("marine", 2), wait()])
    backend.buildings.update(barracks=2, barracks_techlab=1)
    obs, _, _, _ = env.step([train("marauder", 2), wait()])
    assert obs.training["marine"]["in_production"] == 2
    assert obs.training["marauder"]["in_production"] == 1


def test_completed_addon_stays_on_parent_selected_at_dispatch(game):
    env, backend = game
    backend.buildings["barracks"] = 2
    env.step([train("reaper"), {"action": "build", "target": "barracks_techlab"}, wait()])
    obs, _, _, _ = env.step([train("marauder"), wait(23)])
    assert obs.training["reaper"]["in_production"] == 1
    assert obs.training["marauder"]["in_production"] == 1


def test_reset_clears_paid_slots_and_virtual_attachments(game):
    env, backend = game
    backend.buildings.update(barracks=1, barracks_reactor=1)
    env.step([train("marine", 2), wait()])
    env.reset(EpisodeConfig(decision_interval_seconds=1, game_time_limit_seconds=600))
    assert not backend._producer_addons
    assert not backend._queue
    backend.buildings["barracks"] = 1
    backend.minerals = 500
    backend.supply_cap = 30
    obs, _, _, _ = env.step([train("marine", 2), wait()])
    assert obs.training["marine"]["in_production"] == 1
    assert "production_slot" not in str(obs.to_dict())


def test_addon_construction_blocks_parent_until_actual_completion(game):
    env, backend = game
    backend.buildings["barracks"] = 1
    obs, _, _, _ = env.step([{"action": "build", "target": "barracks_reactor"}, train("marine", 2), wait(3)])
    assert obs.training["marine"]["in_production"] == 0
    assert backend.under_construction["barracks_reactor"] == 1
    obs, _, _, _ = env.step([wait(get_target("barracks_reactor").base_time_seconds + 1)])
    assert backend.buildings["barracks_reactor"] == 1
    assert obs.training["marine"]["in_production"] == 2


def test_addon_does_not_attach_to_busy_or_already_attached_parent(game):
    env, backend = game
    backend.buildings["barracks"] = 1
    env.step([train("marine"), {"action": "build", "target": "barracks_techlab"}, wait()])
    assert not backend.under_construction
    env.step([wait(40)])
    assert backend.buildings["barracks_techlab"] == 1
    env.step([{"action": "build", "target": "barracks_reactor"}, wait(60)])
    assert backend.buildings.get("barracks_reactor", 0) == 0


def test_cancel_preserves_both_paid_reactor_channels_and_releases_on_finish(game):
    env, backend = game
    backend.buildings.update(barracks=1, barracks_reactor=1)
    env.step([train("marine", 4), wait()])
    obs, _, _, _ = env.step([
        {"action": "cancel", "target_action": "train", "target": "marine"}, train("reaper"), wait()])
    assert obs.training["marine"]["in_production"] == 2
    assert obs.training["marine"]["waiting_to_produce"] == 0
    assert obs.training["reaper"]["in_production"] == 0
    obs, _, _, _ = env.step([wait(18)])
    assert obs.units["marine"] == 2
    assert obs.training["reaper"]["in_production"] == 1


@pytest.mark.parametrize("spec", targets_for_action("train"), ids=lambda s: s.name)
def test_every_train_target_uses_shared_producer_path(game, spec):
    env, backend = game
    for target in targets_for_action("build"):
        if not target.name.endswith("_reactor"):
            backend.buildings[target.name] = 1
    before = backend.units.get(spec.name, 0)
    obs, _, _, _ = env.step([train(spec.name), wait()])
    assert obs.training[spec.name]["in_production"] == 1
    obs, _, _, _ = advance(env, spec.base_time_seconds + 1)
    assert obs.units[spec.name] == before + 1
    assert_obs_consistent(obs)
