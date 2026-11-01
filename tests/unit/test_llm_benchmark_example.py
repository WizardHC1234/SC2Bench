"""Minimal adapter contract for the external LLM pilot."""

from pathlib import Path

from examples.run_llm_benchmark import BenchmarkLLMAgent
from sc2bench_env.benchmark import AgentInput, BenchmarkRunner
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.env import Environment
from sc2bench_env.interface.config import EpisodeConfig


def test_llm_benchmark_reuses_platform_messages_and_records_actual_input():
    env = Environment(FakeBackend(), record_trajectory=False)
    try:
        observation = env.reset()
        seen = []

        def call(messages):
            seen.append(messages)
            return {
                "tool_calls": [{"id": "a", "name": "advance", "arguments": {"seconds": 5}}],
                "content": "No new order, so I advance.",
                "raw_content": "No new order, so I advance.",
                "model": "mock", "actual_messages": messages,
            }

        tool_turn = env.begin_tool_turn()
        turn = BenchmarkLLMAgent(call)(AgentInput(
            observation, None, tuple(env.tool_specs()), tool_turn.call,
        ))
        assert tool_turn.finish().to_dicts() == [{"name": "advance", "arguments": {"seconds": 5}}]
        sent = seen[0]
        saved = turn.agent_context["messages"]
        assert saved[:len(sent)] == sent
        assert sent[-1]["role"] == "user" and "[Current Observation]" in sent[-1]["content"]
        assert saved[-1]["role"] == "tool"
        assert turn.agent_context["model"] == "mock"
    finally:
        env.close()


def test_llm_retry_attempts_are_recorded_without_exposing_error_text(tmp_path):
    replies = iter([
        {"error": "TimeoutError", "retryable": True,
         "raw_content": "secret response https://private.invalid"},
        {"tool_calls": [{"id": "a", "name": "advance", "arguments": {"seconds": 5}}],
         "content": "No new order, so I advance.", "model": "mock"},
    ])
    batch = BenchmarkRunner(
        backend_factory=FakeBackend,
        record_dir=tmp_path / "records",
    ).run([EpisodeConfig(game_time_limit_seconds=1)],
          lambda: BenchmarkLLMAgent(lambda _messages: next(replies),
                                    api_retry_delay_seconds=0))
    row = batch["episodes"][0]
    assert row["status"] == "completed" and row["decision_count"] == 1
    directory = Path(row["record_directory"]).resolve()
    saved = (directory / "episode.txt").read_text(encoding="utf-8") + (
        directory / "session.json").read_text(encoding="utf-8")
    assert "private.invalid" not in saved
