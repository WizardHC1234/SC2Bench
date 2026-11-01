"""The intentional direct-Environment example stays distinct from Runner."""
import pytest
from examples import llm_vs_ai as example
from examples.agent_integration import create_agent
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.recording.reader import read_episode


def test_direct_cli_preserves_one_episode_without_run_index(tmp_path, monkeypatch):
    monkeypatch.setattr(example.agent_module, "BenchmarkRunner", lambda **kwargs:
                        pytest.fail("Direct Environment example must not construct a Runner"))
    monkeypatch.setattr(example.agent_module, "make_llm_call", lambda **kwargs: (
        lambda messages, tools=None: {
            "tool_calls": [{"id": "a", "name": "advance", "arguments": {"seconds": 5}}],
            "content": "No new order, so I advance.",
        }))
    monkeypatch.setattr(example, "Environment", lambda backend, **kwargs: Environment("fake", **kwargs))
    assert example.main(["--record-dir", str(tmp_path), "--game-time-limit", "2", "--quiet"]) == 0
    directories = list(tmp_path.iterdir())
    assert len(directories) == 1 and directories[0].name != "runs"
    assert read_episode(directories[0])["summary"]["end_reason"] == "time_limit"


def test_direct_loop_provides_feedback_then_records_corrected_actions(tmp_path):
    seen = []
    def call(messages):
        seen.append(messages)
        if len(seen) == 1:
            return {
                "tool_calls": [
                    {"id": "b", "name": "build", "arguments": {"target": "not_a_building"}},
                ],
                "content": "This target is not in the catalog.",
            }
        return {
            "tool_calls": [{"id": "a", "name": "advance", "arguments": {"seconds": 5}}],
            "content": "The rejected build is dropped, so I advance.",
        }
    env = Environment(record_dir=tmp_path)
    try:
        agent = create_agent(call, verbose=False)
        info = example.run_episode(env, agent, EpisodeConfig(game_time_limit_seconds=2))
        assert info["end_reason"] == "time_limit"
        assert any(
            "status: rejected" in str(item.get("content")).lower()
            for item in agent.messages
        )
        assert read_episode(env.record_path)["summary"]["rejected_count"] == 0
    finally:
        env.close()


@pytest.mark.parametrize("reply,reason", [
    ({"error": "TimeoutError", "retryable": False}, "agent_call_failed"),
    ({
        "tool_calls": [
            {"id": "b", "name": "build", "arguments": {"target": "not_a_building"}},
            {"id": "a", "name": "advance", "arguments": {"seconds": 5}},
        ],
        "content": "This target is not in the catalog.",
    }, "tool_round_limit"),
])
def test_direct_loop_preserves_expected_interruption_reasons(tmp_path, reply, reason):
    env = Environment(record_dir=tmp_path)
    try:
        agent = create_agent(
            lambda messages: reply, verbose=False,
            max_consecutive_rejections=1, max_tool_rounds=2,
        )
        info = example.run_episode(env, agent, EpisodeConfig(game_time_limit_seconds=100))
        assert info["end_reason"] == reason
        expected_status = "interrupted" if reason == "tool_round_limit" else (
            "completed" if reason == "time_limit" else "interrupted")
        assert read_episode(env.record_path)["summary"]["status"] == expected_status
        if reason == "tool_round_limit":
            saved = read_episode(env.record_path)["messages"]
            assert saved and saved[0]["role"] == "system"
            assert any(
                "not_submitted" in str(message.get("content"))
                for message in saved
            )
    finally:
        env.close()
