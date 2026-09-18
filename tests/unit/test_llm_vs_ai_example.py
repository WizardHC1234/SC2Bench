"""No paid API or SC2 processes: test the example with a mocked LLM."""

import json
from unittest.mock import MagicMock
from urllib.error import URLError

import pytest

from tests.helpers.legacy_llm_episode import run_episode
from examples import agent_integration
from examples.agent_integration import make_llm_call, parse_decision, resolve_api_key, split_reasoning
from sc2bench_env import Environment
from sc2bench_env.interface.actions import parse_decision as parse_platform_decision
from sc2bench_env.interface.config import EpisodeConfig


def reply(content, **extra):
    return {"content": content, "raw_content": content, "model": "mock", **extra}


def records(env):
    from sc2bench_env.recording.reader import read_episode
    return read_episode(env.record_path)["interactions"]


def test_example_records_actual_messages_raw_output_and_usage(tmp_path):
    env = Environment(record_dir=tmp_path)
    calls = []

    def mock_llm(messages):
        calls.append(messages)
        return reply('[{"action":"wait"}]', actual_messages=[
            {"role": "system", "content": "identity + platform prompt"}, *messages[1:],
        ], usage={"total_tokens": 12})

    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=1))
        result = run_episode(env, mock_llm)
        assert result["result"] == "Result.Tie"
        assert len(calls) == 1
        interaction = records(env)[0]
        assert interaction["input"]["messages"][0]["content"] == "identity + platform prompt"
        assert interaction["output"]["assistant_content"] == '[{"action":"wait"}]'
        assert interaction["metadata"]["usage"] == {"total_tokens": 12}
    finally:
        env.close()


@pytest.mark.parametrize("bad_reply", ['invalid', '[{"action":"train","target":"not_a_unit"}]'])
def test_example_records_bad_output_and_lets_model_correct_it(tmp_path, bad_reply):
    env = Environment(record_dir=tmp_path)
    messages_seen = []

    def mock_llm(messages):
        messages_seen.append(messages)
        return reply(bad_reply if len(messages_seen) == 1 else '[{"action":"wait"}]')

    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=1))
        run_episode(env, mock_llm)
        interactions = records(env)
        assert len(interactions) == 2
        assert interactions[0]["output"]["assistant_content"] == bad_reply
        assert "decision_rejected" in messages_seen[1][1]["content"]
        steps = [row for row in env.trajectory()["steps"] if row.get("type") == "step"]
        assert steps[0]["game_time_before_seconds"] == steps[0]["game_time_after_seconds"] == 0
    finally:
        env.close()


@pytest.mark.parametrize("text", [
    '```json\n[{"action":"wait"}]\n```',
    "[{'action': 'wait'},]",
    '[{"action":"wait",}]',
])
def test_repaired_output_is_executed_and_raw_reply_preserved(tmp_path, text):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=1))
        run_episode(env, lambda messages: reply(text))
        interaction = records(env)[0]
        assert interaction["output"]["assistant_content"] == text
        assert interaction["output"]["submitted_decision"] == [{"action": "wait"}]
        assert interaction["metadata"]["json_repaired"] is True
        steps = [row for row in env.trajectory()["steps"] if row.get("type") == "step"]
        assert steps[-1]["validation"]["accepted"] is True
    finally:
        env.close()


def test_strict_json_does_not_use_repair(monkeypatch):
    repair = MagicMock()
    monkeypatch.setattr("examples.agent_integration.repair_json", repair)
    assert parse_decision('[{"action":"wait"}]') == ([{"action": "wait"}], False)
    repair.assert_not_called()


@pytest.mark.parametrize("bad_reply, reason", [
    ('[{"action":"wait","interval":30}]', "unknown fields ['interval']"),
    ('[{"action":"research","target":"stim_pak"},{"action":"wait"}]',
     "unsupported research target 'stim_pak'"),
])
def test_schema_rejection_is_explicit_in_next_actual_request(tmp_path, bad_reply, reason):
    env = Environment(record_dir=tmp_path)
    seen = []

    def mock_llm(messages):
        seen.append(messages)
        return reply(bad_reply if len(seen) == 1 else '[{"action":"wait"}]')

    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=1))
        run_episode(env, mock_llm)
        assert seen[1][-1]["role"] == "user"
        assert reason in seen[1][-1]["content"]
        hint = seen[1][-1]["content"]
        assert "Timed waits use any_of/all_of arrays of condition objects" in hint
        assert "interval is a condition with seconds, not a wait field" in hint
        assert '"seconds":30' not in hint
        assert records(env)[1]["input"]["messages"] == seen[1]
        assert records(env)[0]["output"]["assistant_content"] == bad_reply
        steps = [row for row in env.trajectory()["steps"] if row.get("type") == "step"]
        assert steps[0]["game_time_after_seconds"] == 0
    finally:
        env.close()


def test_correction_is_cleared_after_an_accepted_decision(tmp_path):
    env = Environment(record_dir=tmp_path)
    seen = []

    def mock_llm(messages):
        seen.append(messages)
        return reply('[{"action":"wait","interval":30}]' if len(seen) == 1
                     else '[{"action":"wait"}]')

    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=11))
        run_episode(env, mock_llm)
        assert len(seen) == 3
        assert "Validation error:" in seen[1][-1]["content"]
        assert len(seen[2]) == 2
    finally:
        env.close()


def test_correction_covers_wait_zone_and_group_types_and_keeps_prior_errors(tmp_path):
    env = Environment(record_dir=tmp_path)
    seen = []
    responses = [
        '[{"action":"wait","seconds":30}]',
        '[{"action":"combat","style":"attack","target":15,"group":2},{"action":"wait"}]',
        '[{"action":"combat","style":"attack","target":"zone_15","group":2},{"action":"wait"}]',
    ]

    def mock_llm(messages):
        seen.append(messages)
        return reply(responses[len(seen) - 1])

    try:
        env.reset()
        with pytest.raises(RuntimeError, match="连续输出非法动作"):
            run_episode(env, mock_llm, verbose=False)
        hint = seen[1][-1]["content"]
        assert "quoted zone_<index> strings" in hint
        assert "quoted group_<index> strings" in hint
        assert "seconds belongs inside the interval condition object" in hint
        assert "unknown fields ['seconds']" in seen[2][-1]["content"]
        assert "received 15" in seen[2][-1]["content"]
        steps = [row for row in env.trajectory()["steps"] if row.get("type") == "step"]
        assert "received 2" in steps[-1]["validation"]["error"]
        assert all(row["game_time_after_seconds"] == 0 for row in steps)
        assert records(env)[2]["output"]["submitted_decision"][0]["group"] == 2
    finally:
        env.close()


def test_repaired_json_still_goes_through_platform_validation(tmp_path, capsys):
    env = Environment(record_dir=tmp_path)
    text = '```json\n[{"action":"train","target":"not_a_unit"},{"action":"wait"}]\n```'
    try:
        env.reset()
        with pytest.raises(RuntimeError, match="连续输出非法动作"):
            run_episode(env, lambda messages: reply(text))
        assert records(env)[0]["metadata"]["json_repaired"] is True
        assert env.trajectory()["steps"][-1]["game_time_after_seconds"] == 0
        assert "rejection_reason=" in capsys.readouterr().out
    finally:
        env.close()


def test_known_truncated_reply_is_not_repaired_or_executed(tmp_path):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset()
        with pytest.raises(RuntimeError, match="连续输出非法动作"):
            run_episode(env, lambda messages: reply('[{"action":"wait"}', finish_reason="length"))
        assert records(env)[0]["metadata"]["json_repaired"] is False
        assert records(env)[0]["output"]["submitted_decision"] is None
        assert env.trajectory()["steps"][-1]["game_time_after_seconds"] == 0
    finally:
        env.close()


@pytest.mark.parametrize("text", ["", "invalid"])
def test_repair_does_not_invent_actions_for_empty_or_plain_text(text):
    with pytest.raises(ValueError):
        parse_decision(text)


def test_example_stops_after_repeated_invalid_decisions(tmp_path):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset()
        with pytest.raises(RuntimeError, match="连续输出非法动作"):
            run_episode(env, lambda messages: reply("invalid"))
        assert len(records(env)) == 3
        assert env.trajectory()["steps"][-1]["game_time_after_seconds"] == 0
    finally:
        env.close()


@pytest.mark.parametrize("flags, expected", [([], False), (["--thinking"], True), (["--no-thinking"], False)])
def test_cli_defaults_to_no_thinking_and_allows_explicit_override(monkeypatch, flags, expected):
    from examples import agent_integration as example
    monkeypatch.setattr("sys.argv", ["agent_integration.py", *flags])
    monkeypatch.setattr(example, "resolve_api_key", lambda: "test-key")
    call_factory = MagicMock()
    monkeypatch.setattr(example, "make_llm_call", call_factory)
    runner = MagicMock()
    runner.run.return_value = {"summary_path": "run.json", "episodes": [],
                               "aggregate": {}, "termination_counts": {}}
    monkeypatch.setattr(agent_integration, "BenchmarkRunner", MagicMock(return_value=runner))
    example.main()
    assert call_factory.call_args.kwargs["thinking"] is expected


def test_api_failure_is_recorded_without_hidden_wait_or_leaking_error(tmp_path):
    env = Environment(record_dir=tmp_path)
    try:
        env.reset()
        with pytest.raises(RuntimeError, match="LLM 调用失败"):
            run_episode(env, lambda messages: reply("", error="upstream secret", request_metadata={"api_key": "secret"}),
                        api_retry_delay_seconds=0)
        interaction = records(env)[0]
        assert interaction["metadata"]["api_failed"] is True
        assert "secret" not in json.dumps(interaction)
        trajectory = env.trajectory()
        assert not any(row["type"] == "step" for row in trajectory["steps"])
        failures = [row for row in trajectory["steps"] if row["type"] == "agent_call_failure"]
        assert len(failures) == 3 and all(row["game_time_seconds"] == 0 for row in failures)
        assert all(row["step_index"] is None for row in failures)
        assert trajectory["summary"]["decision_count"] == trajectory["summary"]["rejected_count"] == 0
        assert trajectory["summary"]["end_reason"] == "agent_call_failed"
    finally:
        env.close()


@pytest.mark.parametrize("thinking", [False, True])
def test_standalone_http_call_preserves_raw_output_and_gateway_settings(monkeypatch, thinking):
    requests = []
    raw = '<think>plan</think>[{"action":"wait"}]'

    def mock_urlopen(request, timeout):
        requests.append((request, timeout))
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "choices": [{"message": {"content": raw}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 5},
        }).encode("utf-8")
        return response

    monkeypatch.setattr("examples.agent_integration.urlopen", mock_urlopen)
    messages = [{"role": "user", "content": "state"}]
    result = make_llm_call(api_key="test-secret", base_url="https://example.invalid/v1/",
                           timeout=9, thinking=thinking)(messages)
    request, timeout = requests[0]
    assert request.full_url == "https://example.invalid/v1/chat/completions"
    assert timeout == 9
    payload = json.loads(request.data)
    assert payload["chat_template_kwargs"] == {"thinking": thinking, "enable_thinking": thinking}
    assert payload["response_format"] == {"type": "text"}
    assert payload["messages"] == result["actual_messages"] == messages
    assert result["raw_content"] == raw
    assert result["content"] == '[{"action":"wait"}]'
    assert result["reasoning"] == "plan"
    assert "test-secret" not in json.dumps(result)


def test_http_failure_does_not_expose_upstream_error_body(monkeypatch):
    def fail(*args, **kwargs):
        raise URLError("upstream test-secret")

    monkeypatch.setattr("examples.agent_integration.urlopen", fail)
    result = make_llm_call(api_key="test-secret")([])
    assert result["error"] == "URLError"
    assert "test-secret" not in json.dumps(result)


def test_api_retry_recovers_without_advancing_or_duplicate_submission(tmp_path):
    env = Environment(record_dir=tmp_path)
    calls = []
    def call(messages):
        calls.append(messages)
        if len(calls) < 3:
            return reply("", error="TimeoutError", retryable=True)
        return reply('[{"action":"wait"}]')
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=1))
        result = run_episode(env, call, max_decisions=1, api_retry_delay_seconds=0)
        assert result["result"] is not None
        assert len(calls) == 3 and calls[0] == calls[1] == calls[2]
        interactions = records(env)
        assert [row.get("type") for row in interactions] == ["agent_call_failure", "agent_call_failure", None]
        assert [row["step_index"] for row in interactions] == [None, None, 1]
        assert interactions[2]["metadata"]["api_attempt"] == 3
        assert env.trajectory()["summary"]["decision_count"] == 1
        assert env.trajectory()["summary"]["rejected_count"] == 0
    finally:
        env.close()


def test_nonretryable_api_failure_stops_after_one_attempt(tmp_path):
    env = Environment(record_dir=tmp_path)
    transport = MagicMock(return_value=reply("", error="HTTPError", http_status=401, retryable=False))
    try:
        env.reset()
        with pytest.raises(RuntimeError, match="LLM 调用失败"):
            run_episode(env, transport, api_retry_delay_seconds=0)
        transport.assert_called_once()
        assert records(env)[0]["metadata"]["api_http_status"] == 401
        assert env.trajectory()["summary"]["rejected_count"] == 0
    finally:
        env.close()


def test_invalid_action_correction_survives_api_retry(tmp_path):
    env = Environment(record_dir=tmp_path)
    calls = []
    def call(messages):
        calls.append(messages)
        if len(calls) == 1:
            return reply('[{"action":"combat","group":"group_0","style":"attack","target":"zone_1"},{"action":"wait"}]')
        if len(calls) == 2:
            return reply("", error="URLError")
        return reply('[{"action":"wait"}]')
    try:
        env.reset(EpisodeConfig(game_time_limit_seconds=1))
        run_episode(env, call, api_retry_delay_seconds=0)
        assert "Validation error" in calls[1][-1]["content"]
        assert calls[1] == calls[2]
        assert env.trajectory()["summary"]["decision_count"] == 2
        assert env.trajectory()["summary"]["rejected_count"] == 1
    finally:
        env.close()


def test_api_exception_message_is_never_recorded(tmp_path):
    env = Environment(record_dir=tmp_path)
    def call(messages):
        raise TimeoutError("https://secret.invalid/key=super-secret")
    try:
        env.reset()
        with pytest.raises(RuntimeError, match="LLM 调用失败"):
            run_episode(env, call, max_api_attempts=1)
        assert records(env)[0]["metadata"]["api_error_type"] == "TimeoutError"
        assert "super-secret" not in json.dumps(env.trajectory()) + json.dumps(records(env))
    finally:
        env.close()


def test_game_end_during_failed_continuous_call_keeps_natural_result(tmp_path):
    env = Environment(record_dir=tmp_path)
    def call(messages):
        env.backend.terminated = True
        env.backend.result = "Result.Victory"
        env.backend.end_reason = "game_ended"
        env.backend.game_time_seconds = 100
        return reply("", error="TimeoutError")
    try:
        env.reset(EpisodeConfig(blocking_decisions=False))
        info = run_episode(env, call, api_retry_delay_seconds=0)
        assert info["result"] == "Result.Victory"
        assert env.trajectory()["summary"]["end_reason"] == "game_ended"
        assert env.trajectory()["summary"]["decision_count"] == 0
        env.close(end_reason="agent_call_failed")
        assert env.trajectory()["summary"]["status"] == "completed"
    finally:
        env.close()


@pytest.mark.parametrize("status,retryable", [(401, False), (403, False), (429, True), (503, True)])
def test_http_retry_policy_uses_status_without_leaking_body(monkeypatch, status, retryable):
    from urllib.error import HTTPError
    def call(*args, **kwargs):
        raise HTTPError("https://secret.invalid", status, "secret body", {}, None)
    monkeypatch.setattr("examples.agent_integration.urlopen", call)
    result = make_llm_call(api_key="secret")([])
    assert result["http_status"] == status and result["retryable"] is retryable
    assert result["error"] == "HTTPError" and "secret" not in json.dumps(result)


def test_incomplete_think_block_never_becomes_a_submitted_action():
    assert split_reasoning('<think>[{"action":"wait"}]') == ("", "")


def test_missing_key_fails_before_any_http_call(monkeypatch):
    transport = MagicMock()
    monkeypatch.setattr("examples.agent_integration.urlopen", transport)
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        make_llm_call(api_key="")
    transport.assert_not_called()


def test_local_key_and_environment_override(monkeypatch):
    monkeypatch.setattr("examples.agent_integration.API_KEY", "local-test-key")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert resolve_api_key() == "local-test-key"
    monkeypatch.setenv("LLM_API_KEY", "env-test-key")
    assert resolve_api_key() == "env-test-key"
    monkeypatch.setenv("LLM_API_KEY", " ")
    assert resolve_api_key() == "local-test-key"
