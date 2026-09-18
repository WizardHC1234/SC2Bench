"""Reject unimplemented own races without pretending Terran means all races."""

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from sc2bench_env import Environment
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.backends.sharpy.backend import SharpyBackend
from sc2bench_env.backends.sharpy.races import get_adapter
from sc2bench_env.benchmark import BenchmarkSuite
from sc2bench_env.interface.action_catalog import (
    render_action_catalog, render_decision_guide, render_system_prompt,
)
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.races import ENEMY_RACES, SUPPORTED_OWN_RACES
from tests.helpers.terran_baseline import DEFAULT_SUITE


@pytest.mark.parametrize("race", ["protoss", "zerg", "random"])
@pytest.mark.parametrize("renderer", [render_action_catalog, render_decision_guide, render_system_prompt])
def test_unimplemented_races_cannot_relabel_terran_catalog(renderer, race):
    with pytest.raises(ValueError, match="Unsupported own race"):
        renderer(race=race)


@pytest.mark.parametrize("race", ["protoss", "zerg", "random"])
def test_environment_rejects_before_backend_start_or_record_creation(race, tmp_path, monkeypatch):
    backend = FakeBackend()
    start = MagicMock(side_effect=AssertionError("must not start"))
    monkeypatch.setattr(backend, "start_episode", start)
    output = tmp_path / "not_created"
    env = Environment(backend, record_dir=output)
    with pytest.raises(ValueError, match="Unsupported own race"):
        env.reset(EpisodeConfig(race=race))
    start.assert_not_called()
    assert env.config is None and env.record_path is None
    assert not output.exists()


def test_bad_reset_does_not_close_or_erase_existing_terran_episode():
    env = Environment("fake", record_trajectory=False)
    try:
        env.reset()
        env.step([{"action": "train", "target": "marine", "count": 8}, {"action": "wait"}])
        old_config, old_time = env.config, env.backend.snapshot().game_time_seconds
        old_demands = env.task_manager.production_priority_summary()
        with pytest.raises(ValueError, match="Unsupported own race"):
            env.reset(EpisodeConfig(race="zerg"))
        assert env.config is old_config
        assert env.task_manager.production_priority_summary() == old_demands
        assert env.backend.snapshot().game_time_seconds == old_time
        env.step([{"action": "wait"}])  # Original episode remains usable.
    finally:
        env.close()


@pytest.mark.parametrize("backend_class", [FakeBackend, SharpyBackend])
def test_direct_backends_reject_before_initialization_or_optional_dependencies(backend_class, monkeypatch):
    import sc2bench_env.backends.sharpy.backend as sharpy_backend
    runtime = MagicMock(side_effect=AssertionError("must not load optional dependencies"))
    monkeypatch.setattr(sharpy_backend, "_ensure_runtime_paths", runtime)
    backend = backend_class()
    with pytest.raises(ValueError, match="Unsupported own race"):
        backend.start_episode(EpisodeConfig(race="protoss"))
    runtime.assert_not_called()
    assert backend._config is None
    if isinstance(backend, SharpyBackend):
        assert backend._thread is None


def test_adapter_entry_preserves_terran_factory_and_fails_before_import_for_other_races(monkeypatch):
    module = ModuleType("sc2bench_env.backends.sharpy.races.terran")
    sentinel = object()
    module.get_adapter = MagicMock(return_value=sentinel)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    assert get_adapter("terran") is sentinel
    module.get_adapter.assert_called_once_with("terran")
    with pytest.raises(ValueError, match="Unsupported own race"):
        get_adapter("zerg")
    assert module.get_adapter.call_count == 1


@pytest.mark.parametrize("enemy_race", ENEMY_RACES)
def test_enemy_race_selection_does_not_enable_our_own_race(enemy_race):
    assert SUPPORTED_OWN_RACES == ("terran",)
    data = BenchmarkSuite.load(DEFAULT_SUITE).to_dict()
    data["episode_defaults"]["enemy_race"] = enemy_race
    suite = BenchmarkSuite.from_dict(data)
    config = suite.episode_plan()[0]["config"]
    env = Environment("fake", record_trajectory=False)
    try:
        observation = env.reset(config)
        assert observation.game.race == "terran"
        assert env.get_system_prompt() == render_system_prompt(race="terran")
    finally:
        env.close()
