"""Safe, explicit transport failures with bounded retries; no network/game."""

import json
from http.client import BadStatusLine, IncompleteRead, RemoteDisconnected
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from examples import agent_integration as example
from sc2bench_env import Environment
from sc2bench_env.benchmark import AgentInput, BenchmarkRunner
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.recording.reader import read_episode


@pytest.mark.parametrize("error", [
    RemoteDisconnected("private-test-secret"),
    ConnectionResetError("private-test-secret"),
    ConnectionAbortedError("private-test-secret"),
    BrokenPipeError("private-test-secret"),
    IncompleteRead(b"private-test-secret", 100),
    BadStatusLine("private-test-secret"),
])
def test_transport_errors_keep_class_name_in_agent_feedback_not_private_text(monkeypatch, capsys, error):
    transport = MagicMock(side_effect=error)
    monkeypatch.setattr(example, "urlopen", transport)
    client = example.make_llm_call(api_key="private-test-secret", base_url="http://example.invalid/v1")
    env = Environment("fake", record_trajectory=False)
    try:
        observation = env.reset()
        turn = example.create_agent(client, verbose=False, max_api_attempts=2,
                                    api_retry_delay_seconds=0)(AgentInput(observation, None, env.get_context()))
        assert transport.call_count == 2
        assert turn.decision is None and turn.stop_after_call_failures
        assert all(row["api_error_type"] == type(error).__name__ for row in turn.call_failures)
        assert all(row["api_retryable"] is True and row["api_http_status"] is None for row in turn.call_failures)
        console = capsys.readouterr().out
        assert f"1/2 failed: {type(error).__name__}; HTTP=n/a" in console
        assert "private-test-secret" not in console
        assert "private-test-secret" not in json.dumps(turn.call_failures)
    finally:
        env.close()


def test_disconnects_are_saved_as_call_failures_not_format_rejections_or_game_losses(tmp_path, capsys):
    def fail(messages):
        raise RemoteDisconnected("private-test-secret")

    batch = BenchmarkRunner(backend_factory=lambda: "fake", record_dir=tmp_path).run(
        [EpisodeConfig(game_time_limit_seconds=100)],
        lambda: example.create_agent(fail, verbose=False, max_api_attempts=2, api_retry_delay_seconds=0))
    row = batch["episodes"][0]
    assert (row["status"], row["end_reason"], row["result"]) == ("interrupted", "agent_call_failed", None)
    assert row["decision_count"] == 0 and row["rejected_count"] == 0
    directory = (Path(batch["summary_path"]).parent / row["record_directory"]).resolve()
    episode = read_episode(directory)
    assert len(episode["interactions"]) == 2
    assert all(item["metadata"]["api_error_type"] == "RemoteDisconnected" for item in episode["interactions"])
    assert "private-test-secret" not in json.dumps(episode)
    assert "private-test-secret" not in capsys.readouterr().out


def test_invalid_api_response_json_is_classified_and_not_retried(monkeypatch, capsys):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b"private-test-secret"
    transport = MagicMock(return_value=response)
    monkeypatch.setattr(example, "urlopen", transport)
    env = Environment("fake", record_trajectory=False)
    try:
        observation = env.reset()
        agent = example.create_agent(example.make_llm_call(api_key="private-test-secret"),
                                     verbose=False, max_api_attempts=3, api_retry_delay_seconds=0)
        turn = agent(AgentInput(observation, None, env.get_context()))
        assert transport.call_count == 1 and turn.stop_after_call_failures
        assert turn.call_failures[0]["api_error_type"] == "JSONDecodeError"
        assert turn.call_failures[0]["api_retryable"] is False
        assert "private-test-secret" not in capsys.readouterr().out
    finally:
        env.close()


def test_unreviewed_error_name_is_not_printed_or_recorded(capsys):
    env = Environment("fake", record_trajectory=False)
    try:
        observation = env.reset()
        agent = example.create_agent(lambda messages: {"error": "private-test-secret", "retryable": False}, verbose=False)
        turn = agent(AgentInput(observation, None, env.get_context()))
        assert turn.call_failures[0]["api_error_type"] == "unknown"
        console = capsys.readouterr().out
        assert "failed: unknown" in console and "private-test-secret" not in console
        assert "private-test-secret" not in json.dumps(turn.call_failures)
    finally:
        env.close()
