"""Builtin AI styles: public configuration through real SC2 player construction."""
import json

import pytest

from examples import agent_integration, llm_vs_ai, run_llm_benchmark
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.benchmark import BenchmarkRunner, BenchmarkSuite
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.opponents import (
    AI_BUILD_ENUM_NAMES, ENEMY_STYLES, normalize_enemy_style,
)
from sc2bench_env.recording.reader import read_episode
from tests.helpers.terran_baseline import DEFAULT_SUITE


def test_exact_commander_styles_and_default():
    assert ENEMY_STYLES == ("random", "rush", "timing", "power", "macro", "air")
    assert EpisodeConfig().enemy_style == "random"
    assert normalize_enemy_style("  MACRO  ") == "macro"


@pytest.mark.parametrize("style", ENEMY_STYLES)
def test_actual_computer_player_receives_style_race_and_difficulty(style):
    data = pytest.importorskip("sc2.data")
    from sc2bench_env.backends.sharpy.backend import _create_builtin_opponent
    player = _create_builtin_opponent(EpisodeConfig(
        enemy_race="zerg", opponent="builtin_mediumhard", enemy_style=style))
    assert player.race is data.Race.Zerg
    assert player.difficulty is data.Difficulty.MediumHard
    assert player.ai_build is getattr(data.AIBuild, AI_BUILD_ENUM_NAMES[style])


@pytest.mark.parametrize("value", ["", "unknown", "Macro", " macro ", None, 1, []])
def test_invalid_canonical_style_rejected_by_config_and_suite(value):
    with pytest.raises(ValueError):
        EpisodeConfig(enemy_style=value)
    data = BenchmarkSuite.load(DEFAULT_SUITE).to_dict()
    data["episode_defaults"]["enemy_style"] = value
    with pytest.raises(ValueError):
        BenchmarkSuite.from_dict(data)


def test_legacy_suite_keeps_source_fingerprint_and_random_default():
    suite = BenchmarkSuite.load(DEFAULT_SUITE)
    assert "enemy_style" not in suite.to_dict()["episode_defaults"]
    assert all(row["config"].enemy_style == "random" for row in suite.episode_plan())
    assert suite.with_overrides().sha256 == suite.sha256


def test_case_styles_and_global_override_without_mutating_source():
    data = BenchmarkSuite.load(DEFAULT_SUITE).to_dict()
    data["episode_defaults"]["enemy_style"] = "macro"
    data["cases"][0]["episode"]["enemy_style"] = "rush"
    suite = BenchmarkSuite.from_dict(data)
    before = suite.to_dict()
    assert suite.episode_plan()[0]["config"].enemy_style == "rush"
    assert suite.episode_plan()[-1]["config"].enemy_style == "macro"
    changed = suite.with_overrides(enemy_style="air")
    assert all(row["config"].enemy_style == "air" for row in changed.episode_plan())
    assert changed.sha256 != suite.sha256
    assert suite.to_dict() == before


@pytest.mark.parametrize("entry", [agent_integration, llm_vs_ai, run_llm_benchmark])
def test_cli_style_preview_without_game_api_or_records(entry, tmp_path, capsys):
    output = tmp_path / "records"
    assert entry.main(["--dry-run", "--enemy-style", "MACRO", "--record-dir", str(output)]) == 0
    preview = json.loads(capsys.readouterr().out)
    config = preview.get("episode") or preview["suite"]["episode_defaults"]
    assert config["enemy_style"] == "macro"
    assert not output.exists()


@pytest.mark.parametrize("entry", [agent_integration, llm_vs_ai, run_llm_benchmark])
def test_cli_unknown_style_fails_before_startup(entry, tmp_path):
    output = tmp_path / "records"
    with pytest.raises(SystemExit) as exc:
        entry.main(["--enemy-style", "unknown", "--record-dir", str(output)])
    assert exc.value.code == 2
    assert not output.exists()


def test_style_is_recorded_but_not_leaked_to_agent(tmp_path):
    contexts = []

    def agent(request):
        contexts.append(request.platform_messages)
        return [{"action": "wait"}]

    runner = BenchmarkRunner(backend_factory=FakeBackend, record_dir=tmp_path / "records")
    batch = runner.run([EpisodeConfig(enemy_style="rush", game_time_limit_seconds=1)],
                       lambda: agent)
    row = batch["episodes"][0]
    assert row["config"]["enemy_style"] == "rush"
    directory = next(path for path in (tmp_path / "records").iterdir() if path.is_dir() and path.name != "runs")
    assert read_episode(directory)["metadata"]["config"]["enemy_style"] == "rush"
    assert "enemy_style" not in json.dumps(contexts)
