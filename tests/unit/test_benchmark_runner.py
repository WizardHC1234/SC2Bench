"""Minimal batch execution: recorded facts, external Agent, isolated failures."""
import json
from pathlib import Path

from sc2bench_env.benchmark import AgentTurn, BenchmarkRunner, Evaluator, recover_batch
from sc2bench_env.interface.tools import ToolCall
from tests.helpers.drive import rendered_messages, scripted_advance
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.recording.reader import read_episode


def _wait(seconds=1):
    return [{"name": "advance", "arguments": {'seconds': seconds}}]


def _runner(tmp_path, factory=lambda: FakeBackend()):
    return BenchmarkRunner(backend_factory=factory,
                           record_dir=tmp_path / "records")


def test_serial_batch_has_one_summary_and_independent_record_folders(tmp_path):
    created = []

    def agent_factory():
        created.append(object())

        def act(request):
            assert request.observation.game.game_time_seconds >= 0
            messages = rendered_messages(request)
            assert messages[0]["role"] == "system"
            return scripted_advance(request)

        return act

    configs = [EpisodeConfig(game_time_limit_seconds=1) for _ in range(3)]
    batch = _runner(tmp_path).run(configs, agent_factory)
    assert len(created) == 3
    assert batch["status"] == "completed"
    assert batch["aggregate"] == {
        "episodes": 3, "completed": 3, "interrupted": 0, "failed": 0,
        "incomplete": 0, "victory": 0, "defeat": 0, "tie": 3,
        "total_decisions": 3, "total_rejected": 0,
        "total_rejected_decisions": 0,
    }
    record_dirs = [path for path in (tmp_path / "records").iterdir() if path.is_dir()]
    assert len(record_dirs) == 1
    batch_dir = record_dirs[0]
    assert (batch_dir / "batch.json").is_file()
    episode_dirs = list((batch_dir / "episodes").iterdir())
    assert len(episode_dirs) == 3
    assert not (tmp_path / "records" / "runs").exists()
    for row in batch["episodes"]:
        assert row["result"] == "Result.Tie" and row["end_reason"] == "time_limit"
        assert row["decision_count"] == 1 and row["rejected_count"] == 0
        directory = Path(row["record_directory"]).resolve()
        assert directory in episode_dirs
        assert {path.name for path in directory.iterdir()} == {
            "episode.txt", "session.json", "metrics.json", "log.txt", "evaluation.jsonl.gz"}
        assert read_episode(directory)["messages"][0]["role"] == "system"


def test_agent_failure_is_not_a_defeat_and_next_episode_runs(tmp_path):
    calls = 0

    def agent_factory():
        nonlocal calls
        calls += 1
        if calls == 1:
            def broken(_request):
                raise RuntimeError("agent failed")
            return broken
        return lambda request: scripted_advance(request)

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

        def act(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                rejected = request.call_tool(ToolCall("wait", {"interval": 1}))
                assert rejected.status == "rejected"
                return AgentTurn()
            return scripted_advance(request)
        return act

    batch = _runner(tmp_path).run([EpisodeConfig(game_time_limit_seconds=1)], agent_factory)
    row = batch["episodes"][0]
    assert row["status"] == "completed" and row["decision_count"] == 1
    assert row["rejected_count"] == 0
    assert batch["aggregate"]["total_rejected_decisions"] == 0


def test_decision_cap_interrupts_without_inventing_a_game_result(tmp_path):
    batch = _runner(tmp_path).run([EpisodeConfig(game_time_limit_seconds=10)],
                                  lambda: lambda request: scripted_advance(request), max_decisions=2)
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
        lambda: lambda request: scripted_advance(request),
    )
    assert [row["status"] for row in batch["episodes"]] == ["failed", "completed"]
    assert batch["episodes"][0]["error_type"] == "RuntimeError"
    assert batch["aggregate"]["failed"] == 1 and batch["aggregate"]["tie"] == 1


def test_evaluator_reclassifies_legacy_transport_failure(tmp_path):
    batch = _runner(tmp_path).run(
        [EpisodeConfig(game_time_limit_seconds=1)],
        lambda: lambda request: scripted_advance(request),
    )
    directory = Path(batch["episodes"][0]["record_directory"])
    episode_path = directory / "episode.txt"
    episode_text = episode_path.read_text(encoding="utf-8")
    episode_path.write_text(
        episode_text.replace('"result": "Result.Tie"', '"result": "Result.Defeat"')
        .replace('"end_reason": "time_limit"', '"end_reason": "game_ended"'),
        encoding="utf-8",
    )
    with (directory / "log.txt").open("a", encoding="utf-8") as handle:
        handle.write(
            "\nSharpy game thread failed\n"
            "ConnectionResetError: Cannot write to closing transport\n"
            "Resigning due to previous error\n"
        )

    row = Evaluator.evaluate_episode(directory)
    assert row["status"] == "failed"
    assert row["outcome"] == "unfinished"
    assert row["result"] is None
    assert row["end_reason"] == "backend_error"


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
    assert summary["total_rejected_decisions"] == 1


def test_evaluator_does_not_combine_conversation_scopes_into_one_win_rate():
    rows = [
        {"status": "completed", "outcome": "victory", "config": {},
         "conversation_scope": "episode"},
        {"status": "completed", "outcome": "defeat", "config": {},
         "conversation_scope": "decision"},
    ]
    metrics = Evaluator.measure(rows)
    assert metrics["conversation_scope"] is None
    assert metrics["conversation_scopes"] == ["episode", "decision"]
    assert metrics["comparable"] is False
    assert metrics["win_rate"] is None


def test_evaluator_recovers_old_session_scope_from_batch_metadata(tmp_path):
    runner = _runner(tmp_path)
    runner.run(
        [EpisodeConfig(game_time_limit_seconds=1)],
        lambda: lambda request: scripted_advance(request),
        agent_metadata={"settings": {"conversation_mode": "full_session"}},
    )
    saved = json.loads(runner.batch_file.read_text(encoding="utf-8"))
    episode_dir = Path(saved["episodes"][0]["record_directory"])
    session_path = episode_dir / "session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session.pop("scope", None)
    session_path.write_text(json.dumps(session), encoding="utf-8")

    audited = Evaluator.evaluate_batch(runner.batch_file)
    assert audited["episodes"][0]["conversation_scope"] == "episode"
    assert audited["metrics"]["conversation_scope"] == "episode"


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
        lambda: lambda request: scripted_advance(request),
    )
    first, second = batch["episodes"]
    assert first["status"] == "failed" and first["record_directory"] is None
    assert first["error_type"] == "RuntimeError"
    assert second["status"] == "completed" and second["outcome"] == "tie"
    batch_dirs = list((tmp_path / "records").iterdir())
    assert len(batch_dirs) == 1
    assert len(list((batch_dirs[0] / "episodes").iterdir())) == 1


def test_runner_does_not_claim_platform_template_was_actual_agent_input(tmp_path):
    def quiet(request):
        request.call_tool(ToolCall("advance", {"seconds": 1}))
        return AgentTurn()

    batch = _runner(tmp_path).run([EpisodeConfig(game_time_limit_seconds=1)],
                                  lambda: quiet)
    directory = (tmp_path / "results" / batch["episodes"][0]["record_directory"]).resolve()
    assert read_episode(directory)["messages"] == []


def test_empty_and_invalid_suite_reject_before_writing(tmp_path):
    import pytest

    runner = _runner(tmp_path)
    with pytest.raises(ValueError):
        runner.run([], lambda: lambda request: scripted_advance(request))
    with pytest.raises(TypeError):
        runner.run(["not a config"], lambda: lambda request: scripted_advance(request))
    with pytest.raises(ValueError, match="credentials"):
        runner.run([EpisodeConfig(game_time_limit_seconds=1)],
                   lambda: lambda request: scripted_advance(request),
                   agent_metadata={"settings": {"api_key": "do-not-save"}})
    assert not (tmp_path / "records" / "runs").exists()


def test_batch_saves_external_agent_metadata_as_a_snapshot(tmp_path):
    metadata = {"name": "example", "settings": {"temperature": 0.5}}
    batch = _runner(tmp_path).run(
        [EpisodeConfig(game_time_limit_seconds=1)], lambda: lambda request: scripted_advance(request),
        agent_metadata=metadata,
    )
    metadata["settings"]["temperature"] = 9
    assert batch["schema_version"] == "0.4"
    assert batch["agent_metadata"] == {"name": "example", "settings": {"temperature": 0.5}}


def test_recover_batch_reindexes_episode_artifacts_after_coordinator_loss(tmp_path):
    runner = _runner(tmp_path)
    runner.run(
        [EpisodeConfig(game_time_limit_seconds=1)],
        lambda: lambda request: scripted_advance(request),
    )
    saved = json.loads(runner.batch_file.read_text(encoding="utf-8"))
    saved["status"] = "running"
    saved["episodes"] = []
    runner.batch_file.write_text(json.dumps(saved), encoding="utf-8")

    recovered = recover_batch(runner.batch_file, force=True)

    assert recovered["status"] == "completed"
    assert len(recovered["episodes"]) == 1
    assert recovered["episodes"][0]["status"] == "completed"
    assert recovered["aggregate"]["completed"] == 1
    assert recovered["recovered_at"] == recovered["ended_at"]
    audited = Evaluator.evaluate_batch(runner.batch_file)
    assert audited["source_status"] == "completed"
    assert audited["aggregate"]["completed"] == 1


def test_external_failed_calls_do_not_count_as_game_decisions(tmp_path):
    def agent_factory():
        def act(request):
            messages = rendered_messages(request)
            failure = {"messages": messages, "assistant_content": "",
                       "api_failed": True, "api_error_type": "TimeoutError", "api_attempt": 1}
            request.call_tool(ToolCall("advance", {"seconds": 1}))
            return AgentTurn({"messages": messages, "assistant_content": "wait"},
                             call_failures=(failure,))
        return act

    batch = _runner(tmp_path).run([EpisodeConfig(game_time_limit_seconds=1)], agent_factory)
    row = batch["episodes"][0]
    assert row["status"] == "completed" and row["decision_count"] == 1
    directory = Path(row["record_directory"]).resolve()
    saved = (directory / "session.json").read_text(encoding="utf-8")
    assert "TimeoutError" not in saved
    assert "TimeoutError" in (directory / "metrics.json").read_text(encoding="utf-8")
    assert read_episode(directory)["messages"][0]["role"] == "system"
    assert read_episode(directory)["summary"]["decision_count"] == 1


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
    directory = Path(row["record_directory"]).resolve()
    assert read_episode(directory)["summary"]["status"] == "interrupted"
    assert read_episode(directory)["summary"]["decision_count"] == 0


def test_deliberate_agent_stop_is_recorded_and_next_episode_continues(tmp_path):
    from sc2bench_env.benchmark import AgentStopped
    calls = []
    def factory():
        calls.append(1)
        if len(calls) == 1:
            def stop(request):
                raise AgentStopped()
            return stop
        return lambda request: scripted_advance(request)
    batch = _runner(tmp_path).run([EpisodeConfig(game_time_limit_seconds=1)] * 2, factory)
    first, second = batch["episodes"]
    assert first["status"] == "interrupted" and first["end_reason"] == "agent_stopped"
    assert first["result"] is None and first["decision_count"] == 0
    assert second["status"] == "completed"
