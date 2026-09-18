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
    monkeypatch.setattr(example.agent_module, "make_llm_call", lambda **kwargs:
                        lambda messages: {"content": '[{"action":"wait"}]'})
    monkeypatch.setattr(example, "Environment", lambda backend, **kwargs: Environment("fake", **kwargs))
    assert example.main(["--record-dir", str(tmp_path), "--game-time-limit", "2", "--quiet"]) == 0
    directories = list(tmp_path.iterdir())
    assert len(directories) == 1 and directories[0].name != "runs"
    assert read_episode(directories[0])["summary"]["end_reason"] == "time_limit"


def test_direct_loop_provides_feedback_then_records_corrected_actions(tmp_path):
    seen = []
    def call(messages):
        seen.append(messages)
        return {"content": '[{"action":"unknown"}]' if len(seen) == 1 else '[{"action":"wait"}]'}
    env = Environment(record_dir=tmp_path)
    try:
        info = example.run_episode(env, create_agent(call, verbose=False),
                                   EpisodeConfig(game_time_limit_seconds=2))
        assert info["end_reason"] == "time_limit"
        assert "Your last action array was rejected" in seen[1][-1]["content"]
        assert read_episode(env.record_path)["summary"]["rejected_count"] == 1
    finally:
        env.close()


@pytest.mark.parametrize("reply,reason", [
    ({"error": "TimeoutError", "retryable": False}, "agent_call_failed"),
    ({"content": '[{"action":"unknown"}]'}, "invalid_decision_limit"),
])
def test_direct_loop_preserves_expected_interruption_reasons(tmp_path, reply, reason):
    env = Environment(record_dir=tmp_path)
    try:
        agent = create_agent(lambda messages: reply, verbose=False, max_consecutive_rejections=1)
        info = example.run_episode(env, agent, EpisodeConfig(game_time_limit_seconds=100))
        assert info["end_reason"] == reason
        assert read_episode(env.record_path)["summary"]["status"] == "interrupted"
    finally:
        env.close()
