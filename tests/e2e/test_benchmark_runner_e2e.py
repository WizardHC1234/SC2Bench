"""One short, no-API SC2 episode through the public batch interface."""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SC2BENCH_E2E", "").strip() not in {"1", "true", "yes"},
    reason="Set SC2BENCH_E2E=1 to run real StarCraft II tests",
)


def test_real_serial_runner_writes_only_episode_files_and_one_batch_summary(tmp_path, monkeypatch):
    from sc2bench_env.benchmark import BenchmarkRunner, BenchmarkSuite
    from tests.helpers.terran_baseline import DEFAULT_SUITE

    data = BenchmarkSuite.load(DEFAULT_SUITE).to_dict()
    data["suite_id"] = "test_only_short_suite"
    data["repetitions"] = 1
    data["episode_defaults"]["game_time_limit_seconds"] = 1
    data["cases"] = [{"case_id": "short_sc2_smoke",
                      "episode": {"opponent": "builtin_veryeasy"}}]
    suite = BenchmarkSuite.from_dict(data)

    monkeypatch.setenv("SC2BENCH_OUTPUT_DIR", str(tmp_path))
    runner = BenchmarkRunner()
    result = runner.run(
        suite,
        lambda: lambda _request: [{"action": "wait", "any_of": [
            {"condition": "interval", "seconds": 1},
        ]}],
    )
    row = result["episodes"][0]
    assert result["suite"]["sha256"] == suite.sha256
    assert row["case_id"] == "short_sc2_smoke" and row["repetition"] == 1
    assert row["runtime_versions"]["game_version"]
    assert row["versions"]["burnysc2"]
    assert result["termination_counts"] == {"completed/time_limit": 1}
    assert row["status"] == "completed" and row["outcome"] == "tie"
    assert row["end_reason"] == "time_limit" and row["decision_count"] == 1
    assert result["output_paths"] == {
        "record_dir": str(tmp_path / "records"),
        "results_dir": str(tmp_path / "records" / "runs"),
    }
    directory = (tmp_path / "records" / "runs" / row["record_directory"]).resolve()
    assert {path.name for path in directory.iterdir()} == {
        "episode.txt", "interactions.jsonl", "replay.SC2Replay",
    }
    assert len([path for path in (tmp_path / "records").iterdir() if path.name != "runs"]) == 1
    summaries = list((tmp_path / "records" / "runs").iterdir())
    assert len(summaries) == 1 and summaries[0].name.startswith("run_")
