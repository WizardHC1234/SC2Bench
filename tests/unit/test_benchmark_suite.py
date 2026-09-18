"""Unique critical paths for seed-free Suite loading and serial execution."""

import json
from pathlib import Path

import pytest

from tests.helpers.terran_baseline import DEFAULT_SUITE
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.benchmark import BenchmarkRunner, BenchmarkSuite, Evaluator


def test_suite_loads_deterministic_plan_without_seed_or_agent_policy():
    suite = BenchmarkSuite.load(DEFAULT_SUITE)
    plan = suite.episode_plan()
    assert [(item["case_id"], item["repetition"]) for item in plan] == [
        ("kairos_tvt_easy", 1), ("kairos_tvt_easy", 2),
        ("kairos_tvt_medium", 1), ("kairos_tvt_medium", 2),
    ]
    assert all(item["config"].seed is None for item in plan)
    assert suite.max_decisions == 500
    data = suite.to_dict()
    data["episode_defaults"]["map_name"] = "changed"
    assert suite.episode_plan()[0]["config"].map_name == "KairosJunctionLE"
    assert BenchmarkSuite.from_dict(suite.to_dict()).sha256 == suite.sha256


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(repetitions=0),
    lambda d: d.update(max_decisions=True),
    lambda d: d["episode_defaults"].update(seed=123),
    lambda d: d["episode_defaults"].update(race="zerg"),
    lambda d: d["episode_defaults"].update(blocking_decisions="true"),
    lambda d: d["episode_defaults"].update(game_time_limit_seconds=float("nan")),
    lambda d: d["cases"][0]["episode"].update(opponent="not_an_opponent"),
    lambda d: d["cases"].append(d["cases"][0]),
    lambda d: d.update(cases=[]),
])
def test_invalid_suite_rejects_before_launch(mutation):
    data = BenchmarkSuite.load(DEFAULT_SUITE).to_dict()
    mutation(data)
    with pytest.raises(ValueError):
        BenchmarkSuite.from_dict(data)


def test_loader_rejects_duplicate_json_keys(tmp_path):
    # File creation stays in pytest's isolated fixture; no actual game or API.
    path = tmp_path / "suite.json"
    path.write_text('{"schema_version":"1","schema_version":"1"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        BenchmarkSuite.load(path)


def test_overrides_change_actual_suite_fingerprint_and_do_not_mutate_original():
    suite = BenchmarkSuite.load(DEFAULT_SUITE)
    changed = suite.with_overrides(repetitions=1, max_decisions=20,
                                   opponents=("builtin_medium",))
    assert len(changed.episode_plan()) == 1 and changed.max_decisions == 20
    assert changed.sha256 != suite.sha256 and len(suite.episode_plan()) == 4
    with pytest.raises(ValueError):
        suite.with_overrides(opponents=("builtin_hard",))


def test_suite_runner_records_plan_versions_case_and_termination_categories(tmp_path):
    data = BenchmarkSuite.load(DEFAULT_SUITE).to_dict()
    data["episode_defaults"]["game_time_limit_seconds"] = 1
    data["max_decisions"] = 5
    suite = BenchmarkSuite.from_dict(data)
    calls = []

    class VersionedFake(FakeBackend):
        def snapshot(self):
            snapshot = super().snapshot()
            snapshot.info["game_version"] = "test-only-version"
            return snapshot

    def create_agent():
        calls.append(object())
        return lambda request: [{"action": "wait", "any_of": [
            {"condition": "interval", "seconds": 1}]}]

    runner = BenchmarkRunner(backend_factory=VersionedFake, record_dir=tmp_path / "records",
                             results_dir=tmp_path / "results")
    batch = runner.run(suite, create_agent, agent_metadata={"name": "test"})
    assert len(calls) == 4
    assert batch["suite"]["sha256"] == suite.sha256
    assert batch["suite"]["specification"] == suite.to_dict()
    assert len(batch["planned_configs"]) == 4 and batch["max_decisions"] == 5
    assert batch["platform_versions"]["python"]
    assert len(batch["platform_source_sha256"]) == 64
    assert batch["termination_counts"] == {"completed/time_limit": 4}
    assert batch["case_results"]["kairos_tvt_easy"]["aggregate"]["tie"] == 2
    assert batch["case_results"]["kairos_tvt_medium"]["termination_counts"] == {
        "completed/time_limit": 2}
    for row in batch["episodes"]:
        assert row["versions"]["python"] and len(row["platform_prompt_sha256"]) == 64
        assert row["runtime_versions"]["game_version"] == "test-only-version"
        assert row["config"]["seed"] is None
    saved = json.loads(Path(batch["summary_path"]).read_text(encoding="utf-8"))
    assert saved["suite"] == batch["suite"]
    with pytest.raises(ValueError, match="with_overrides"):
        runner.run(suite, create_agent, max_decisions=99)


def test_termination_summary_does_not_merge_timeout_agent_failure_and_defeat():
    rows = [{"status": "completed", "end_reason": "game_ended", "outcome": "defeat"},
            {"status": "completed", "end_reason": "time_limit", "outcome": "tie"},
            {"status": "interrupted", "end_reason": "agent_call_failed"},
            {"status": "failed", "end_reason": "backend_error"}]
    assert Evaluator.termination_counts(rows) == {
        "completed/game_ended": 1, "completed/time_limit": 1,
        "interrupted/agent_call_failed": 1, "failed/backend_error": 1}
