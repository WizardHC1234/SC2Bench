"""Defaults do not drift with cwd; explicit output locations remain authoritative."""

from pathlib import Path

import pytest

import sc2bench_env.paths as paths
from sc2bench_env import Environment
from sc2bench_env.benchmark import BenchmarkRunner
from sc2bench_env.benchmark.__main__ import main


def test_source_defaults_are_project_anchored_without_creating_cwd_outputs(tmp_path, monkeypatch):
    monkeypatch.delenv("SC2BENCH_OUTPUT_DIR", raising=False)
    before = paths.default_paths()
    monkeypatch.chdir(tmp_path)
    assert paths.default_paths() == before
    assert paths.resolve_record_dir() == Path(__file__).resolve().parents[2] / "records"
    assert not (tmp_path / "records").exists()
    assert not (tmp_path / "records" / "runs").exists()


def test_environment_runner_and_cli_share_overridden_output_root(tmp_path, monkeypatch, capsys):
    root = tmp_path / "unified"
    monkeypatch.setenv("SC2BENCH_OUTPUT_DIR", str(root))
    runner = BenchmarkRunner()
    env = Environment.__new__(Environment)
    # Bypass only pytest's default-path redirection; never start this environment.
    Environment.__init__.__wrapped__(env, "fake", record_trajectory=False)
    assert runner.record_dir == env._record_dir == root / "records"
    assert runner.results_dir == root / "records" / "runs"
    assert main(["paths"]) == 0
    import json
    assert json.loads(capsys.readouterr().out) == paths.default_paths()
    assert not root.exists()


def test_explicit_relative_and_absolute_paths_override_shared_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("SC2BENCH_OUTPUT_DIR", str(tmp_path / "default"))
    monkeypatch.chdir(tmp_path)
    runner = BenchmarkRunner(record_dir="chosen_records", results_dir=tmp_path / "chosen_results")
    assert runner.record_dir == tmp_path / "chosen_records"
    assert runner.results_dir == tmp_path / "chosen_results"
    monkeypatch.chdir(tmp_path.parent)
    assert runner.record_dir == tmp_path / "chosen_records"


def test_custom_record_root_keeps_each_game_separate_with_one_nested_run_summary(tmp_path):
    from sc2bench_env.backends.fake import FakeBackend
    from sc2bench_env.interface.config import EpisodeConfig
    runner = BenchmarkRunner(record_dir=tmp_path / "chosen_records", backend_factory=FakeBackend)
    assert runner.results_dir == runner.record_dir / "runs"
    batch = runner.run([EpisodeConfig(game_time_limit_seconds=1)] * 2,
                       lambda: lambda request: [{"action": "wait"}])
    episode_dirs = [path for path in runner.record_dir.iterdir() if path.name != "runs"]
    assert len(episode_dirs) == 2 and episode_dirs[0] != episode_dirs[1]
    assert all({file.name for file in directory.iterdir()} == {"episode.txt", "interactions.jsonl"}
               for directory in episode_dirs)
    summaries = list(runner.results_dir.iterdir())
    assert len(summaries) == 1 and summaries[0].name.startswith("run_")
    assert {(runner.results_dir / row["record_directory"]).resolve()
            for row in batch["episodes"]} == set(episode_dirs)


@pytest.mark.parametrize("value", ["", " ", "relative/directory"])
def test_implicit_environment_override_must_be_absolute(value, monkeypatch):
    monkeypatch.setenv("SC2BENCH_OUTPUT_DIR", value)
    with pytest.raises(ValueError, match="absolute"):
        paths.default_output_root()


def test_non_editable_install_uses_user_directory_not_site_packages(tmp_path, monkeypatch):
    monkeypatch.delenv("SC2BENCH_OUTPUT_DIR", raising=False)
    monkeypatch.setattr(paths, "__file__", str(tmp_path / "Lib" / "site-packages" / "sc2bench_env" / "paths.py"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user"))
    assert paths.default_output_root() == tmp_path / "user" / ".sc2bench"


def test_record_reference_uses_portable_separators_and_cross_drive_fallback(tmp_path, monkeypatch):
    directory = tmp_path / "records" / "episode"
    assert paths.record_reference(directory, tmp_path / "results") == "../records/episode"
    def different_drive(*args):
        raise ValueError("different drives")
    monkeypatch.setattr(paths.os.path, "relpath", different_drive)
    assert paths.record_reference(directory, tmp_path / "results") == directory.as_posix()
