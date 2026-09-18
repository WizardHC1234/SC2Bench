"""Minimal batch execution: recorded facts, external Agent, isolated failures."""
import json

from sc2bench_env.benchmark import AgentTurn, BenchmarkRunner, Evaluator
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.recording.reader import read_episode


def _wait(seconds=1):
    return [{"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}]


def _runner(tmp_path, factory=lambda: FakeBackend()):
    return BenchmarkRunner(backend_factory=factory,
                           record_dir=tmp_path / "records", results_dir=tmp_path / "results")


def test_serial_batch_has_one_summary_and_independent_record_folders(tmp_path):
    created = []

    def agent_factory():
        created.append(object())

        def act(request):
            assert request.observation.game.game_time_seconds >= 0
            assert request.platform_messages[0]["role"] == "system"
            return AgentTurn(_wait(), {"messages": request.platform_messages,
                                       "assistant_content": "wait"})

        return act

    configs = [EpisodeConfig(game_time_limit_seconds=1) for _ in range(3)]
    batch = _runner(tmp_path).run(configs, agent_factory)
    assert len(created) == 3
    assert batch["status"] == "completed"
    assert batch["aggregate"] == {
        "episodes": 3, "completed": 3, "interrupted": 0, "failed": 0,
        "incomplete": 0, "victory": 0, "defeat": 0, "tie": 3,
        "total_decisions": 3, "total_rejected": 0,
    }
    result_files = list((tmp_path / "results").iterdir())
    record_dirs = list((tmp_path / "records").iterdir())
    assert len(result_files) == 1 and len(record_dirs) == 3
    assert result_files[0].name.startswith("run_")
    assert json.loads(result_files[0].read_text(encoding="utf-8")) == {
        key: value for key, value in batch.items() if key != "summary_path"
    }
    for row in batch["episodes"]:
        assert row["result"] == "Result.Tie" and row["end_reason"] == "time_limit"
        assert row["decision_count"] == 1 and row["rejected_count"] == 0
        directory = (tmp_path / "results" / row["record_directory"]).resolve()
        assert directory in record_dirs
        assert {path.name for path in directory.iterdir()} == {"episode.txt", "interactions.jsonl"}
        assert read_episode(directory)["interactions"][0]["input"]["messages"][0]["role"] == "system"


def test_agent_failure_is_not_a_defeat_and_next_episode_runs(tmp_path):
    calls = 0

    def agent_factory():
        nonlocal calls
        calls += 1
        if calls == 1:
            def broken(_request):
                raise RuntimeError("agent failed")
            return broken
        return lambda _request: _wait()

    batch = _runner(tmp_path).run([EpisodeConfig(game_time_limit_seconds=1)] * 2, agent_factory)
    assert batch["aggregate"]["interrupted"] == 1
    assert batch["aggregate"]["tie"] == 1
    assert batch["aggregate"]["defeat"] == 0
    first, second = batch["episodes"]
    assert first["status"] == "interrupted" and first["outcome"] == "unfinished"
    assert first["end_reason"] == "agent_error" and first["error_type"] == "RuntimeError"
    assert second["status"] == "completed" and second["outcome"] == "tie"


def test_rejected_action_count_comes_from_terminal_record(tmp_path):
    def agent_factory():
        calls = 0

        def act(_request):
            nonlocal calls
            calls += 1
            return [{"action": "wait", "interval": 1}] if calls == 1 else _wait()
        return act

    batch = _runner(tmp_path).run([EpisodeConfig(game_time_limit_seconds=1)], agent_factory)
    row = batch["episodes"][0]
    assert row["status"] == "completed" and row["decision_count"] == 2
    assert row["rejected_count"] == 1
    assert batch["aggregate"]["total_rejected"] == 1


def test_decision_cap_interrupts_without_inventing_a_game_result(tmp_path):
    batch = _runner(tmp_path).run([EpisodeConfig(game_time_limit_seconds=10)],
                                  lambda: lambda _request: _wait(), max_decisions=2)
    row = batch["episodes"][0]
    assert row["status"] == "interrupted" and row["end_reason"] == "decision_limit"
    assert row["outcome"] == "unfinished" and row["result"] is None
    assert row["decision_count"] == 2


def test_backend_error_is_isolated_from_next_episode(tmp_path):
    class BrokenBackend(FakeBackend):
        def run_until(self, trigger):
            raise RuntimeError("backend failed")

    calls = 0

    def backend_factory():
        nonlocal calls
        calls += 1
        return BrokenBackend() if calls == 1 else FakeBackend()

    batch = _runner(tmp_path, backend_factory).run(
        [EpisodeConfig(game_time_limit_seconds=1)] * 2,
        lambda: lambda _request: _wait(),
    )
    assert [row["status"] for row in batch["episodes"]] == ["failed", "completed"]
    assert batch["episodes"][0]["error_type"] == "RuntimeError"
    assert batch["aggregate"]["failed"] == 1 and batch["aggregate"]["tie"] == 1


def test_evaluator_uses_only_finalized_outcome_and_does_not_count_events():
    rows = [
        {"status": "completed", "outcome": "victory", "decision_count": 4, "rejected_count": 1},
        {"status": "completed", "outcome": "defeat", "decision_count": 2, "rejected_count": 0},
        {"status": "completed", "outcome": "tie", "decision_count": 3, "rejected_count": 0},
        {"status": "interrupted", "outcome": "unfinished", "decision_count": 1, "rejected_count": 0},
    ]
    summary = Evaluator.summarize(rows)
    assert summary["victory"] == summary["defeat"] == summary["tie"] == 1
    assert summary["interrupted"] == 1 and summary["total_decisions"] == 10
    assert summary["total_rejected"] == 1


def test_environment_creation_failure_does_not_stop_later_episode(tmp_path):
    calls = 0

    def backend_factory():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("no backend")
        return FakeBackend()

    batch = _runner(tmp_path, backend_factory).run(
        [EpisodeConfig(game_time_limit_seconds=1)] * 2,
        lambda: lambda _request: _wait(),
    )
    first, second = batch["episodes"]
    assert first["status"] == "failed" and first["record_directory"] is None
    assert first["error_type"] == "RuntimeError"
    assert second["status"] == "completed" and second["outcome"] == "tie"
    assert len(list((tmp_path / "records").iterdir())) == 1


def test_runner_does_not_claim_platform_template_was_actual_agent_input(tmp_path):
    batch = _runner(tmp_path).run([EpisodeConfig(game_time_limit_seconds=1)],
                                  lambda: lambda _request: _wait())
    directory = (tmp_path / "results" / batch["episodes"][0]["record_directory"]).resolve()
    interaction = read_episode(directory)["interactions"][0]
    assert interaction["input"] == {"source": "not_supplied", "messages": None}


def test_empty_and_invalid_suite_reject_before_writing(tmp_path):
    import pytest

    runner = _runner(tmp_path)
    with pytest.raises(ValueError):
        runner.run([], lambda: lambda _request: _wait())
    with pytest.raises(TypeError):
        runner.run(["not a config"], lambda: lambda _request: _wait())
    with pytest.raises(ValueError, match="credentials"):
        runner.run([EpisodeConfig(game_time_limit_seconds=1)],
                   lambda: lambda _request: _wait(),
                   agent_metadata={"settings": {"api_key": "do-not-save"}})
    assert not (tmp_path / "results").exists() or not list((tmp_path / "results").iterdir())


def test_batch_saves_external_agent_metadata_as_a_snapshot(tmp_path):
    metadata = {"name": "example", "settings": {"temperature": 0.5}}
    batch = _runner(tmp_path).run(
        [EpisodeConfig(game_time_limit_seconds=1)], lambda: lambda _request: _wait(),
        agent_metadata=metadata,
    )
    metadata["settings"]["temperature"] = 9
    assert batch["schema_version"] == "0.3"
    assert batch["agent_metadata"] == {"name": "example", "settings": {"temperature": 0.5}}
    saved = json.loads((tmp_path / "results" /
                        (batch["summary_path"].split("\\")[-1])).read_text(encoding="utf-8"))
    assert saved["agent_metadata"] == batch["agent_metadata"]


def test_external_failed_calls_do_not_count_as_game_decisions(tmp_path):
    def agent_factory():
        def act(request):
            failure = {"messages": request.platform_messages, "assistant_content": "",
                       "api_failed": True, "api_error_type": "TimeoutError", "api_attempt": 1}
            return AgentTurn(_wait(), {"messages": request.platform_messages,
                                      "assistant_content": "wait"},
                             call_failures=(failure,))
        return act

    batch = _runner(tmp_path).run([EpisodeConfig(game_time_limit_seconds=1)], agent_factory)
    row = batch["episodes"][0]
    assert row["status"] == "completed" and row["decision_count"] == 1
    directory = (tmp_path / "results" / row["record_directory"]).resolve()
    lines = [json.loads(line) for line in (directory / "interactions.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert [line["type"] for line in lines if line["type"] in {"agent_call_failure", "step"}] == [
        "agent_call_failure", "step",
    ]
    failure = next(line for line in lines if line["type"] == "agent_call_failure")
    assert failure["error_type"] == "TimeoutError" and failure["attempt"] == 1


def test_all_failed_calls_interrupt_without_fabricating_defeat(tmp_path):
    attempts = ({"api_failed": True, "api_error_type": "TimeoutError", "api_attempt": 1},
                {"api_failed": True, "api_error_type": "HTTPError", "api_attempt": 2})
    batch = _runner(tmp_path).run(
        [EpisodeConfig(game_time_limit_seconds=1)],
        lambda: lambda _request: AgentTurn(None, call_failures=attempts,
                                           stop_after_call_failures=True),
    )
    row = batch["episodes"][0]
    assert row["status"] == "interrupted" and row["end_reason"] == "agent_call_failed"
    assert row["decision_count"] == 0 and row["result"] is None
    directory = (tmp_path / "results" / row["record_directory"]).resolve()
    lines = [json.loads(line) for line in (directory / "interactions.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert [line["attempt"] for line in lines if line["type"] == "agent_call_failure"] == [1, 2]


def test_deliberate_agent_stop_is_recorded_and_next_episode_continues(tmp_path):
    from sc2bench_env.benchmark import AgentStopped
    calls = []
    def factory():
        calls.append(1)
        if len(calls) == 1:
            def stop(request):
                raise AgentStopped()
            return stop
        return lambda request: _wait()
    batch = _runner(tmp_path).run([EpisodeConfig(game_time_limit_seconds=1)] * 2, factory)
    first, second = batch["episodes"]
    assert first["status"] == "interrupted" and first["end_reason"] == "agent_stopped"
    assert first["result"] is None and first["decision_count"] == 0
    assert second["status"] == "completed"
