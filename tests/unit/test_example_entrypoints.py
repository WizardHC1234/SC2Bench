"""Exercise user-facing scripts without starting SC2 or calling a model."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from examples import agent_integration, llm_vs_llm, run_llm_benchmark, llm_vs_ai
from tests.helpers import scripted_episode as scripted_agent
from examples.agent_integration import print_batch_result
from tests.helpers.terran_baseline import DEFAULT_SUITE, create_rule_agent
from sc2bench_env import Environment
from sc2bench_env.benchmark import BenchmarkRunner
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.recording.reader import read_episode


ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINTS = (
    "agent_integration", "llm_vs_ai", "llm_vs_llm", "run_llm_benchmark",
    "run_deepseek_versus", "run_match_grid",
)


@pytest.mark.parametrize("name", ENTRYPOINTS)
def test_direct_help_works_from_outside_project(name, tmp_path):
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "examples" / (name + ".py")), "--help"],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("name", ENTRYPOINTS)
def test_module_help_works_from_project_root(name):
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "examples." + name, "--help"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


@pytest.mark.parametrize("entry", (agent_integration, llm_vs_ai, llm_vs_llm, run_llm_benchmark))
def test_dry_run_creates_nothing_and_excludes_credentials(entry, tmp_path, monkeypatch, capsys):
    output = tmp_path / "not_created"
    monkeypatch.setenv("LLM_API_KEY", "test-private-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://private-gateway.invalid/v1")
    if hasattr(entry, "make_llm_call"):
        transport = MagicMock(side_effect=AssertionError("must not construct the transport"))
        monkeypatch.setattr(entry, "make_llm_call", transport)
    assert entry.main(["--dry-run", "--record-dir", str(output)]) == 0
    text = capsys.readouterr().out
    data = json.loads(text)
    assert "test-private-key" not in text and "private-gateway.invalid" not in text
    assert data["record_dir"] == str(output)
    assert not output.exists()


@pytest.mark.parametrize("arguments", [
    ["--api-timeout", "nan"], ["--game-time-limit", "inf"],
    ["--max-decisions", "0"], ["--api-retry-delay", "nan"],
    ["--temperature", "3"], ["--max-rejections", "-1"],
])
def test_llm_cli_rejects_bad_limits_before_game_or_api(arguments, monkeypatch):
    game, api = MagicMock(), MagicMock()
    monkeypatch.setattr(agent_integration, "BenchmarkRunner", game)
    monkeypatch.setattr(agent_integration, "make_llm_call", api)
    with pytest.raises(SystemExit) as error:
        agent_integration.main(arguments)
    assert error.value.code == 2
    game.assert_not_called()
    api.assert_not_called()


def test_scripted_episode_reaches_real_terminal_instead_of_early_return(tmp_path, capsys):
    env = Environment("fake", record_dir=tmp_path)
    try:
        info = scripted_agent.run_episode(env, EpisodeConfig(
            game_time_limit_seconds=240))
        assert info["end_reason"] == "time_limit"
    finally:
        env.close()
    data = read_episode(env.record_path)
    assert data["summary"]["status"] == "completed"
    assert data["summary"]["decision_count"] == 48
    assert data["summary"]["rejected_count"] == 0
    assert data["summary"]["game_time_seconds"] == 240


def test_single_llm_cli_records_decision_limit_as_interruption(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(agent_integration, "BenchmarkRunner", lambda **kwargs: BenchmarkRunner(
        backend_factory=lambda: "fake", record_dir=kwargs.get("record_dir")))
    monkeypatch.setattr(agent_integration, "make_llm_call", lambda **kwargs: (
        lambda messages, tools=None: {
            "tool_calls": [{"id": "a", "name": "advance", "arguments": {"seconds": 5}}],
            "content": "No new order, so I advance.",
            "raw_content": "No new order, so I advance.",
        }))
    assert agent_integration.main(["--record-dir", str(tmp_path), "--max-decisions", "1",
                          "--game-time-limit", "600", "--quiet"]) == 1
    batch_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
    directory = next((batch_dir / "episodes").iterdir())
    summary = read_episode(directory)["summary"]
    assert summary["status"] == "interrupted" and summary["end_reason"] == "decision_limit"
    assert summary["decision_count"] == 1 and summary["result"] is None


def test_batch_metadata_matches_actual_llm_cli_settings(tmp_path, monkeypatch, capsys):
    assert run_llm_benchmark.main([
        "--dry-run", "--temperature", "0.2", "--api-timeout", "5",
        "--api-attempts", "2", "--api-retry-delay", "0", "--max-rejections", "4",
        "--thinking", "--record-dir", str(tmp_path / "records"),
    ]) == 0
    settings = json.loads(capsys.readouterr().out)["agent_metadata"]["settings"]
    assert settings["temperature"] == 0.2 and settings["api_timeout"] == 5
    assert settings["max_api_attempts"] == 2 and settings["api_retry_delay_seconds"] == 0
    assert settings["max_consecutive_rejections"] == 4
    assert settings["thinking"] is True


def test_failed_batch_returns_nonzero_not_success(capsys):
    row = {"index": 1, "config": {"opponent": "easy"}, "status": "failed",
           "outcome": "unfinished", "end_reason": "backend_error",
           "decision_count": 0, "rejected_count": 0}
    assert print_batch_result({"summary_path": "run.json", "episodes": [row],
                               "aggregate": {}, "termination_counts": {}}) == 1


def test_rule_fixture_runs_short_suite_with_fake_backend(tmp_path):
    from sc2bench_env.benchmark import BenchmarkSuite, BenchmarkRunner
    data = BenchmarkSuite.load(DEFAULT_SUITE).to_dict()
    data["repetitions"] = 1
    data["episode_defaults"]["game_time_limit_seconds"] = 1
    batch = BenchmarkRunner(backend_factory=lambda: "fake", record_dir=tmp_path / "records").run(
        BenchmarkSuite.from_dict(data), create_rule_agent)
    assert all(row["status"] == "completed" for row in batch["episodes"])
    records = list((tmp_path / "records").iterdir())
    assert len(records) == 1 and (records[0] / "batch.json").is_file()
    episodes = list((records[0] / "episodes").iterdir())
    assert episodes and all((path / "episode.txt").is_file() for path in episodes)
    assert len(episodes) == len(batch["episodes"])


def test_api_failure_body_is_not_saved_by_single_example(tmp_path, capsys):
    from agents.llm_agent import LLMAgent
    env = Environment(record_dir=tmp_path)
    try:
        info = llm_vs_ai.run_episode(env, LLMAgent(lambda messages, tools=None: {
            "error": "HTTPError", "retryable": False,
            "raw_content": "private upstream response", "reasoning": "private reasoning",
        }, verbose=False, max_api_attempts=1), EpisodeConfig(game_time_limit_seconds=10))
        assert info["end_reason"] == "agent_call_failed"
        content = (env.record_path / "session.json").read_text(encoding="utf-8") + (
            env.record_path / "episode.txt").read_text(encoding="utf-8")
        assert "private upstream response" not in content and "private reasoning" not in content
    finally:
        env.close()


def test_examples_only_contain_three_public_entries():
    assert {path.stem for path in (ROOT / "examples").glob("*.py")} == set(ENTRYPOINTS)
