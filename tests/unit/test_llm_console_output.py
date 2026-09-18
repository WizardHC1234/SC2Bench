"""Readable example output uses actual input/reply, with no paid API or SC2."""

import json

import pytest

from tests.helpers.legacy_llm_episode import run_episode
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig


def test_default_console_shows_input_before_call_then_raw_reply_and_feedback(tmp_path, capsys):
    env = Environment(record_dir=tmp_path)
    before = []
    content = '[{"action":"wait"}]'
    raw = "<think>model thoughts</think>" + content

    def call(messages):
        before.append(capsys.readouterr().out)
        assert messages[1]["content"] in before[-1]
        assert "Waiting for model reply" in before[-1]
        assert messages[0]["content"] not in before[-1]
        return {"content": content, "raw_content": raw, "model": "mock"}

    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=1))
        result = run_episode(env, call)
        output = "".join(before) + capsys.readouterr().out
        assert result["result"] == "Result.Tie"
        assert "[Current Observation]" in output
        assert "[Model reply (raw)]\n" + raw in output
        assert "[Step feedback]" in output
        assert "Action receipts:" in output and "Execution events:" in output
        assert output.index("[Current Observation]") < output.index("[Model reply (raw)]")
        assert output.index("[Model reply (raw)]") < output.index("Submitting decision")
        assert output.index("Submitting decision") < output.index("[Step feedback]")
        assert "Parsed decision (JSON repaired)" not in output
        from sc2bench_env.recording.reader import read_episode
        interactions = read_episode(env.record_path)
        assert interactions["interactions"][0]["output"]["assistant_content"] == raw
    finally:
        env.close()


def test_repaired_json_is_separate_from_original_model_reply(tmp_path, capsys):
    env = Environment(record_dir=tmp_path)
    raw = "[{'action':'wait'},]"
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=1))
        run_episode(env, lambda messages: {"content": raw, "raw_content": raw})
        output = capsys.readouterr().out
        assert "[Model reply (raw)]\n" + raw in output
        assert "[Parsed decision (JSON repaired)]" in output
        assert '"action": "wait"' in output
        assert "json_repaired=True" in output
    finally:
        env.close()


@pytest.mark.parametrize("reasoning", ["API-provided analysis", ""])
def test_thinking_content_or_absence_is_visible_and_recorded(tmp_path, capsys, reasoning):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=1))
        run_episode(env, lambda _: {"content": '[{"action":"wait"}]',
                                   "reasoning": reasoning, "thinking_requested": True})
        output = capsys.readouterr().out
        assert ("[Model reasoning (API returned)]" if reasoning else
                "API returned no reasoning text") in output
        from sc2bench_env.recording.reader import read_episode
        data = read_episode(env.record_path)
        assert data["interactions"][0]["metadata"]["thinking_requested"] is True
        assert data["interactions"][0]["metadata"]["reasoning"] == reasoning
    finally:
        env.close()


def test_rejection_and_actual_next_correction_are_visible(tmp_path, capsys):
    env = Environment(record_dir=tmp_path)
    calls = []

    def call(messages):
        calls.append(messages)
        text = "not valid JSON" if len(calls) == 1 else '[{"action":"wait"}]'
        return {"content": text, "raw_content": text}

    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=1))
        run_episode(env, call)
        output = capsys.readouterr().out
        assert "[Model reply (raw)]\nnot valid JSON" in output
        assert "rejection_reason=" in output
        assert "accepted=False" in output
        assert "[Previous Feedback]" in output
        assert calls[1][-1]["content"] in output
        assert output.count("[Model reply (raw)]") == 2
    finally:
        env.close()


def test_quiet_hides_large_blocks_but_keeps_status(tmp_path, capsys):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=1))
        run_episode(env, lambda messages: {"content": '[{"action":"wait"}]'}, verbose=False)
        output = capsys.readouterr().out
        assert "round=1" in output and "accepted=True" in output
        for hidden in ("[Current Observation]", "[Model reply (raw)]", "[Step feedback]"):
            assert hidden not in output
    finally:
        env.close()


def test_api_failure_never_prints_error_payload_or_gateway_metadata(tmp_path, capsys):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset()
        with pytest.raises(RuntimeError, match="LLM 调用失败"):
            run_episode(env, lambda messages: {
                "error": "private-error-text", "raw_content": "private-http-body",
                "request_metadata": {"api_key": "private-api-key"}, "retryable": False,
            }, max_api_attempts=1)
        output = capsys.readouterr().out
        assert "[Current Observation]" in output
        for hidden in ("private-error-text", "private-http-body", "private-api-key", "[Model reply (raw)]"):
            assert hidden not in output
        assert not any(row.get("type") == "step" for row in env.trajectory()["steps"])
    finally:
        env.close()


def test_retry_prints_attempt_and_refreshed_input(tmp_path, capsys):
    env = Environment(record_dir=tmp_path)
    attempts = []

    def call(messages):
        attempts.append(messages)
        if len(attempts) == 1:
            return {"error": "TimeoutError", "retryable": True}
        return {"content": '[{"action":"wait"}]'}

    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=1))
        run_episode(env, call, api_retry_delay_seconds=0)
        output = capsys.readouterr().out
        assert "API attempt=1/3" in output and "API attempt=2/3" in output
        assert output.count("[Current Observation]") == 2
        assert output.count("[Model reply (raw)]") == 1
        assert "API call failed: type=TimeoutError" in output
    finally:
        env.close()
