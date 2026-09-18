"""Difficulty coverage without launching a model or game."""

import json

import pytest

from examples import agent_integration, llm_vs_ai, run_llm_benchmark
from sc2bench_env.benchmark import BenchmarkSuite
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.opponents import (
    BUILTIN_OPPONENTS, DIFFICULTY_ENUM_NAMES, normalize_opponent,
)
from tests.helpers.terran_baseline import DEFAULT_SUITE


LEVELS = (
    "VeryEasy", "Easy", "Medium", "MediumHard", "Hard", "Harder",
    "VeryHard", "CheatVision", "CheatMoney", "CheatInsane",
)


def test_registry_has_exactly_ten_ordered_commander_levels():
    assert tuple(DIFFICULTY_ENUM_NAMES.values()) == LEVELS
    assert BUILTIN_OPPONENTS == tuple(name.lower() for name in LEVELS)
    assert agent_integration.BUILTIN_OPPONENTS == run_llm_benchmark.BUILTIN_OPPONENTS == BUILTIN_OPPONENTS


@pytest.mark.parametrize("enum_name", LEVELS)
def test_short_and_canonical_names_resolve_to_exact_sc2_enum(enum_name):
    canonical = enum_name.lower()
    assert normalize_opponent("  " + enum_name + "  ") == canonical
    assert normalize_opponent(canonical) == canonical
    assert normalize_opponent("builtin_" + canonical) == canonical
    difficulty = pytest.importorskip("sc2.data").Difficulty
    from sc2bench_env.backends.sharpy.backend import _parse_difficulty
    assert _parse_difficulty(canonical) is getattr(difficulty, enum_name)
    assert _parse_difficulty("builtin_" + canonical) is getattr(difficulty, enum_name)
    assert _parse_difficulty(enum_name) is getattr(difficulty, enum_name)


@pytest.mark.parametrize("alias,target", [
    ("vision", "cheatvision"), ("money", "cheatmoney"), ("insane", "cheatinsane"),
])
def test_commander_aliases(alias, target):
    assert normalize_opponent(alias) == target
    assert normalize_opponent("builtin_" + alias) == target


@pytest.mark.parametrize("value", ["", "elite", "builtin_elite", "cheat", "hardest", None, 10])
def test_unknown_difficulty_does_not_silently_fall_back(value):
    with pytest.raises(ValueError):
        normalize_opponent(value)


@pytest.mark.parametrize("opponent", BUILTIN_OPPONENTS)
def test_suite_accepts_all_ten_canonical_difficulties(opponent):
    data = BenchmarkSuite.load(DEFAULT_SUITE).to_dict()
    data["cases"] = [{"case_id": "difficulty_case", "episode": {"opponent": opponent}}]
    suite = BenchmarkSuite.from_dict(data)
    assert suite.episode_plan()[0]["config"].opponent == opponent


@pytest.mark.parametrize("entry", [agent_integration, llm_vs_ai])
@pytest.mark.parametrize("name", ["veryhard", "cheatvision", "money", "insane"])
def test_single_game_cli_normalizes_commander_names(entry, name, tmp_path, capsys):
    output = tmp_path / "not_created"
    assert entry.main(["--dry-run", "--difficulty", name, "--record-dir", str(output)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["episode"]["opponent"] == normalize_opponent(name)
    assert not output.exists()


def test_batch_cli_can_filter_a_cheat_case_in_custom_suite(tmp_path, capsys):
    data = BenchmarkSuite.load(DEFAULT_SUITE).to_dict()
    data["cases"] = [{"case_id": "cheat_case", "episode": {"opponent": "builtin_cheatinsane"}}]
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "not_created"
    assert run_llm_benchmark.main([
        "--dry-run", "--suite", str(path), "--opponent", "insane", "--record-dir", str(output),
    ]) == 0
    preview = capsys.readouterr().out
    assert "cheatinsane" in preview and "builtin_" not in preview
    assert not output.exists()


@pytest.mark.parametrize("opponent", BUILTIN_OPPONENTS)
def test_episode_and_suite_normalize_legacy_names_to_short_ids(opponent):
    config = EpisodeConfig(opponent="builtin_" + opponent, enemy_style="macro")
    assert config.opponent == opponent
    assert config.to_dict()["opponent"] == opponent
    assert config.enemy_style == "macro"
    data = BenchmarkSuite.load(DEFAULT_SUITE).to_dict()
    data["episode_defaults"]["opponent"] = "builtin_" + opponent
    data["cases"] = [{"case_id": "legacy", "episode": {"opponent": "builtin_" + opponent}}]
    suite = BenchmarkSuite.from_dict(data)
    assert suite.to_dict()["episode_defaults"]["opponent"] == opponent
    assert suite.to_dict()["cases"][0]["episode"]["opponent"] == opponent
    assert data["episode_defaults"]["opponent"] == "builtin_" + opponent
    assert suite.with_overrides(opponents=("builtin_" + opponent,)).episode_plan()[0]["config"].opponent == opponent
    data["episode_defaults"]["opponent"] = opponent
    data["cases"][0]["episode"]["opponent"] = opponent
    assert BenchmarkSuite.from_dict(data).sha256 == suite.sha256


def test_runner_records_short_difficulty_without_changing_ai_style(tmp_path):
    from sc2bench_env.benchmark import BenchmarkRunner, Evaluator
    from sc2bench_env.recording.reader import read_episode
    runner = BenchmarkRunner(backend_factory=lambda: "fake", record_dir=tmp_path)
    batch = runner.run([EpisodeConfig(opponent="builtin_easy", enemy_style="macro",
                                     game_time_limit_seconds=1)],
                       lambda: lambda request: [{"action": "wait"}])
    row = batch["episodes"][0]
    assert row["config"]["opponent"] == "easy"
    assert row["config"]["enemy_style"] == "macro"
    directory = (runner.results_dir / row["record_directory"]).resolve()
    assert read_episode(directory)["metadata"]["config"]["opponent"] == "easy"
    assert Evaluator.evaluate_batch(batch["summary_path"])["aggregate"] == batch["aggregate"]
