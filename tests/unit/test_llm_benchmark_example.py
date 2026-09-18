"""Minimal adapter contract for the external LLM pilot."""

from examples.run_llm_benchmark import BenchmarkLLMAgent
from sc2bench_env.benchmark import AgentInput, BenchmarkRunner
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.env import Environment
from sc2bench_env.interface.config import EpisodeConfig
import json


def test_llm_benchmark_reuses_platform_messages_and_records_actual_input():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        observation = env.reset()
        seen = []

        def call(messages):
            seen.append(messages)
            return {"content": "Hold current orders.\n\n[{\"action\":\"wait\"}]",
                    "raw_content": "Hold current orders.\n\n[{\"action\":\"wait\"}]",
                    "model": "mock", "actual_messages": messages}

        turn = BenchmarkLLMAgent(call)(AgentInput(observation, None, env.get_context()))
        assert turn.decision == [{"action": "wait"}]
        assert turn.agent_context["messages"] == seen[0]
        assert turn.agent_context["model"] == "mock"
        assert turn.agent_context["decision_summary"] == "Hold current orders."
    finally:
        env.close()


def test_llm_retry_attempts_are_recorded_without_exposing_error_text(tmp_path):
    replies = iter([
        {"error": "TimeoutError", "retryable": True,
         "raw_content": "secret response https://private.invalid"},
        {"content": "Hold.\n\n[{\"action\":\"wait\"}]", "model": "mock"},
    ])
    batch = BenchmarkRunner(
        backend_factory=FakeBackend,
        record_dir=tmp_path / "records", results_dir=tmp_path / "results",
    ).run([EpisodeConfig(game_time_limit_seconds=1)],
          lambda: BenchmarkLLMAgent(lambda _messages: next(replies),
                                    api_retry_delay_seconds=0))
    row = batch["episodes"][0]
    assert row["status"] == "completed" and row["decision_count"] == 1
    directory = (tmp_path / "results" / row["record_directory"]).resolve()
    lines = [json.loads(line) for line in (directory / "interactions.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert [line["type"] for line in lines if line["type"] in {"agent_call_failure", "step"}] == [
        "agent_call_failure", "step",
    ]
    assert "private.invalid" not in json.dumps(lines)
