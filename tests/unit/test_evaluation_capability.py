"""Batch records, game seeds, agent calls and offline comparison stats."""
import json
from pathlib import Path

import pytest

from sc2bench_env.benchmark import BenchmarkRunner, BenchmarkSuite, Evaluator
from sc2bench_env.interface.agent import AgentTurn
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.tools import ToolCall
from tests.helpers.drive import scripted_advance


def _suite(**updates):
    data = {
        "schema_version": "1",
        "suite_id": "eval_capability",
        "suite_version": "1",
        "purpose": "evaluation",
        "repetitions": 2,
        "max_decisions": 2,
        "episode_defaults": {
            "race": "terran", "enemy_race": "terran", "map_name": "KairosJunctionLE",
            "opponent": "easy", "blocking_decisions": True, "game_time_limit_seconds": 1,
        },
        "cases": [{"case_id": "one", "episode": {}}],
    }
    data.update(updates)
    return BenchmarkSuite.from_dict(data)


def _identity():
    return {"name": "probe", "version": "1", "model": "test-model", "settings": {"temperature": 0.5}}


def test_game_seeds_repeat_across_cases_and_reject_a_bad_length():
    suite = _suite(game_seeds=[11, 12], purpose="workflow_pilot")
    plan = suite.episode_plan()
    assert [item["config"].seed for item in plan] == [11, 12]
    assert [item["repetition"] for item in plan] == [1, 2]
    data = suite.to_dict()
    data["game_seeds"] = [1]
    with pytest.raises(ValueError, match="game_seeds"):
        BenchmarkSuite.from_dict(data)
    data = suite.to_dict()
    data["episode_defaults"]["seed"] = 3
    with pytest.raises(ValueError, match="seed"):
        BenchmarkSuite.from_dict(data)
    with pytest.raises(ValueError, match="game_seeds"):
        suite.with_overrides(repetitions=1)


def test_batch_file_keeps_suite_agent_seed_and_survives_an_abort(tmp_path):
    suite = _suite(game_seeds=[11, 12], purpose="workflow_pilot")
    runner = BenchmarkRunner(backend_factory=lambda: "fake", record_dir=tmp_path / "records")

    class _StopBatch(BaseException):
        pass

    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        if calls["n"] == 2:
            def stop(_request):
                raise _StopBatch()
            return stop
        return lambda request: scripted_advance(request)

    with pytest.raises(_StopBatch):
        runner.run(suite, factory, agent_metadata=_identity())
    saved = json.loads(runner.batch_file.read_text(encoding="utf-8"))
    assert saved["schema_version"] == "0.4"
    assert saved["status"] == "failed"
    assert saved["suite"]["sha256"] == suite.sha256
    assert saved["agent_metadata"]["model"] == "test-model"
    assert saved["execution_policy"]["decision_mode"] == "blocking"
    assert saved["episodes"][0]["config"]["seed"] == 11
    assert saved["episodes"][0]["status"] == "completed"
    assert saved["metrics"]["seed_alignment"][0]["seed"] == 11
    episode = Path(saved["episodes"][0]["record_directory"])
    assert episode.parent == runner.batch_directory / "episodes"
    assert not list((tmp_path / "records").glob("run_*.json"))


def test_evaluation_requires_identity_and_still_rejects_endpoints(tmp_path):
    suite = _suite(repetitions=1, game_seeds=[4])
    runner = BenchmarkRunner(backend_factory=lambda: "fake", record_dir=tmp_path / "records")
    with pytest.raises(ValueError, match="name, version and model"):
        runner.run(suite, lambda: lambda request: scripted_advance(request))
    partial = {"name": "probe", "version": "1"}
    with pytest.raises(ValueError, match="model"):
        runner.run(suite, lambda: lambda request: scripted_advance(request), agent_metadata=partial)
    with pytest.raises(ValueError, match="credentials"):
        runner.run(suite, lambda: lambda request: scripted_advance(request),
                   agent_metadata={**_identity(), "settings": {"api_url": "http://secret"}})
    with pytest.raises(ValueError, match="settings"):
        runner.run(suite, lambda: lambda request: scripted_advance(request),
                   agent_metadata={**_identity(), "settings": ["not-an-object"]})
    assert runner.batch_directory is None


def test_one_batch_cannot_mix_blocking_and_realtime(tmp_path):
    runner = BenchmarkRunner(backend_factory=lambda: "fake", record_dir=tmp_path / "records")
    configs = [EpisodeConfig(game_time_limit_seconds=1),
               EpisodeConfig(game_time_limit_seconds=1, realtime=True)]
    with pytest.raises(ValueError, match="mix"):
        runner.run(configs, lambda: lambda request: scripted_advance(request))
    assert not (tmp_path / "records").exists()


def test_agent_calls_keep_missing_tokens_null_and_evaluator_does_not_invent_them(tmp_path):
    def act(request):
        request.call_tool(ToolCall("advance", {"seconds": 1}))
        return AgentTurn({
            "messages": [{"role": "assistant", "content": "advance"}],
            "agent_calls": [{
                "call_id": "known",
                "role": "planner",
                "model": "test-model",
                "status": "ok",
                "input_tokens": None,
                "output_tokens": "12",
                "latency_seconds": 0.25,
            }],
        })

    runner = BenchmarkRunner(backend_factory=lambda: "fake", record_dir=tmp_path / "records")
    batch = runner.run([EpisodeConfig(game_time_limit_seconds=1)], lambda: act)
    document = json.loads((Path(batch["episodes"][0]["record_directory"]) / "metrics.json").read_text(encoding="utf-8"))
    calls = document["model_calls"]
    assert len(calls) == 1
    assert calls[0]["input_tokens"] is None
    assert calls[0]["output_tokens"] is None
    assert calls[0]["role"] == "planner"
    metrics = Evaluator.evaluate_batch(runner.batch_file)["metrics"]
    assert metrics["tokens"]["input"] is None
    assert metrics["tokens"]["output"] is None
    assert metrics["model_calls"] == {"ok": 1, "failure": 0}
    assert metrics["tool_calls"]["advance"] == 1
    assert metrics["decision_mode"] == "blocking"
    assert metrics["win_rate"] == 0
    assert metrics["comparable"] is True


def test_failed_model_call_uses_the_same_event_shape(tmp_path):
    failure = {
        "api_failed": True, "api_error_type": "TimeoutError", "api_http_status": 504,
        "api_attempt": 1, "latency_seconds": 1.5, "model": "test-model",
    }
    runner = BenchmarkRunner(backend_factory=lambda: "fake", record_dir=tmp_path / "records")
    batch = runner.run(
        [EpisodeConfig(game_time_limit_seconds=1)],
        lambda: lambda _request: AgentTurn(None, call_failures=(failure,), stop_after_call_failures=True),
    )
    document = json.loads((Path(batch["episodes"][0]["record_directory"]) / "metrics.json").read_text(encoding="utf-8"))
    calls = document["model_calls"]
    assert len(calls) == 1
    assert calls[0]["status"] == "failure"
    assert calls[0]["error_type"] == "TimeoutError"
    assert calls[0]["http_status"] == 504
    assert calls[0]["input_tokens"] is None
    metrics = batch["metrics"]
    assert metrics["model_calls"]["failure"] == 1
    assert metrics["win_rate"] is None


def test_tool_rejections_keep_their_family_and_have_separate_metrics(tmp_path):
    def act(request):
        invalid_read = request.call_tool(ToolCall(
            "query_zone_state", {"zone_ids": ["zone_0"], "extra": True},
        ))
        invalid_action = request.call_tool(ToolCall(
            "combat", {"style": "attack", "target": "zone_1", "units": {"marine": 1}},
        ))
        assert invalid_read.status == invalid_action.status == "rejected"
        request.call_tool(ToolCall("advance", {"seconds": 1}))
        return AgentTurn()

    runner = BenchmarkRunner(backend_factory=lambda: "fake", record_dir=tmp_path / "records")
    batch = runner.run([EpisodeConfig(game_time_limit_seconds=1)], lambda: act)
    metrics = batch["metrics"]
    assert metrics["rejected_decisions"] == 0
    assert metrics["tool_rejections"]["total"] == 2
    assert metrics["tool_rejections"]["by_kind"] == {"read": 1, "action": 1}
    assert metrics["tool_calls"]["read"] == 1
    assert metrics["tool_calls"]["action"] == 1
    assert metrics["tool_calls"]["advance"] == 1
