"""The full LLM Agent uses mocked model calls, never real SC2/API in unit tests."""
import json
from pathlib import Path
import pytest
from examples import agent_integration as example
from sc2bench_env import Environment
from sc2bench_env.benchmark import AgentInput, AgentTurn, BenchmarkRunner, Evaluator
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.recording.reader import read_episode


def mock_reply(messages):
    return {"content": '[{"action":"wait"}]', "raw_content": '[{"action":"wait"}]',
            "model": "mock", "actual_messages": messages}


def test_factory_returns_new_llm_agent_and_preserves_actual_input():
    first, second = example.create_agent(mock_reply), example.create_agent(mock_reply)
    assert first is not second
    env = Environment(record_trajectory=False)
    try:
        observation = env.reset(EpisodeConfig(game_time_limit_seconds=2))
        turn = first(AgentInput(observation, None, env.get_context()))
        assert isinstance(turn, AgentTurn) and turn.decision == [{"action": "wait"}]
        assert json.loads(turn.agent_context["assistant_content"]) == turn.decision
        assert turn.agent_context["model"] == "mock"
        assert "[Current Observation]" in turn.agent_context["messages"][1]["content"]
    finally:
        env.close()


def test_two_games_preserve_llm_transcripts_and_offline_evaluation(tmp_path):
    batch = BenchmarkRunner(backend_factory=lambda: "fake", record_dir=tmp_path / "records").run(
        [EpisodeConfig(game_time_limit_seconds=2, decision_interval_seconds=1)] * 2,
        lambda: example.create_agent(mock_reply), max_decisions=3)
    summary = Path(batch["summary_path"])
    assert batch["termination_counts"] == {"completed/time_limit": 2}
    directories = [(summary.parent / row["record_directory"]).resolve() for row in batch["episodes"]]
    assert len(set(directories)) == 2
    for directory in directories:
        episode = read_episode(directory)
        assert len(episode["interactions"]) == 2
        for row in episode["interactions"]:
            assert json.loads(row["output"]["assistant_content"]) == [{"action": "wait"}]
            assert "[Current Observation]" in row["input"]["messages"][1]["content"]
    before = summary.read_bytes()
    assert Evaluator.evaluate_batch(summary)["aggregate"] == batch["aggregate"]
    assert summary.read_bytes() == before


def test_cli_runs_llm_agent_with_mock_transport_and_explicit_root(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(example, "make_llm_call", lambda **kwargs: mock_reply)
    monkeypatch.setattr(example, "BenchmarkRunner", lambda **kwargs: BenchmarkRunner(
        backend_factory=lambda: "fake", record_dir=kwargs.get("record_dir")))
    assert example.main(["--record-dir", str(tmp_path), "--game-time-limit", "2", "--quiet"]) == 0
    episode = read_episode(next(path for path in tmp_path.iterdir() if path.name != "runs"))
    assert episode["summary"]["end_reason"] == "time_limit"
    assert len(episode["interactions"]) == 1


def test_llm_agent_reads_feedback_and_corrects_rejected_batch(tmp_path):
    seen = []
    replies = iter(['[{"action":"unknown"}]', '[{"action":"wait"}]'])
    def call(messages):
        seen.append(messages)
        return {"content": next(replies)}
    batch = BenchmarkRunner(backend_factory=lambda: "fake", record_dir=tmp_path).run(
        [EpisodeConfig(game_time_limit_seconds=2)],
        lambda: example.create_agent(call, verbose=False))
    row = batch["episodes"][0]
    assert row["end_reason"] == "time_limit"
    assert "Your last action array was rejected" in seen[1][-1]["content"]
    assert row["rejected_count"] == 1 and row["status"] == "completed"


@pytest.mark.parametrize("failure,expected", [
    ({"error": "TimeoutError", "retryable": False}, "agent_call_failed"),
    ({"content": '[{"action":"unknown"}]'}, "invalid_decision_limit"),
])
def test_llm_agent_failures_have_explicit_interruption_reason(tmp_path, failure, expected):
    batch = BenchmarkRunner(backend_factory=lambda: "fake", record_dir=tmp_path).run(
        [EpisodeConfig(game_time_limit_seconds=100)],
        lambda: example.create_agent(lambda messages: failure, verbose=False,
                                     max_consecutive_rejections=1, api_retry_delay_seconds=0))
    row = batch["episodes"][0]
    assert row["end_reason"] == expected and row["status"] == "interrupted"
    assert row["result"] is None


def test_llm_agent_decision_budget_is_not_a_draw(tmp_path):
    batch = BenchmarkRunner(backend_factory=lambda: "fake", record_dir=tmp_path).run(
        [EpisodeConfig(game_time_limit_seconds=100)],
        lambda: example.create_agent(mock_reply, verbose=False), max_decisions=1)
    row = batch["episodes"][0]
    assert row["end_reason"] == "decision_limit" and row["result"] is None
    assert row["status"] == "interrupted"


def test_main_explicitly_uses_runner_and_fresh_agent_factory(monkeypatch):
    from unittest.mock import MagicMock
    client = MagicMock(side_effect=AssertionError("no real model call"))
    runner = MagicMock()
    runner.run.return_value = {"summary_path": "run.json", "episodes": [],
                               "aggregate": {}, "termination_counts": {}}
    constructor = MagicMock(return_value=runner)
    monkeypatch.setattr(example, "BenchmarkRunner", constructor)
    monkeypatch.setattr(example, "make_llm_call", lambda **kwargs: client)
    assert example.main(["--game-time-limit", "120", "--quiet"]) == 0
    configs = runner.run.call_args.args[0]
    assert len(configs) == 1 and configs[0].game_time_limit_seconds == 120
    factory = runner.run.call_args.kwargs["agent_factory"]
    first, second = factory(), factory()
    assert isinstance(first, example.LLMAgent) and first is not second
    assert first.call_llm is client
    assert constructor.call_args.kwargs["backend_factory"]() == "sharpy"
