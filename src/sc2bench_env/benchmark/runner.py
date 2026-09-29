"""Serial or bounded parallel episodes; no built-in policy or model calls."""
from __future__ import annotations


import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence
from uuid import uuid4

from sc2bench_env.benchmark.evaluator import Evaluator
from sc2bench_env.benchmark.suite import BenchmarkSuite
from sc2bench_env.env import Environment
from sc2bench_env.interface.agent import AgentInput, AgentStopped, AgentTurn
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.runtime.tool_turn import ToolTurnError
from sc2bench_env.recording.trajectory import TrajectoryRecorder, collect_versions
from sc2bench_env.paths import resolve_record_dir


def _platform_fingerprint() -> str:
    """Fingerprint installed platform Python sources, not an invented git revision."""
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        name = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


def _snapshot_agent_metadata(
    value: Optional[Mapping[str, Any]], *, require_identity: bool,
) -> Optional[dict[str, Any]]:
    if value is None:
        if require_identity:
            raise ValueError("evaluation agent_metadata requires name, version and model")
        return None
    if not isinstance(value, Mapping):
        raise TypeError("agent_metadata must be a mapping")
    try:
        snapshot = json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError("agent_metadata must contain JSON-compatible values") from error
    forbidden = {"api_key", "access_token", "authorization", "password",
                 "secret", "client_secret", "api_base_url", "api_url",
                 "base_url", "endpoint", "service_url"}

    def check_keys(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if str(key).lower() in forbidden:
                    raise ValueError("agent_metadata must not contain credentials or endpoint URLs")
                check_keys(child)
        elif isinstance(item, list):
            for child in item:
                check_keys(child)

    check_keys(snapshot)
    settings = snapshot.get("settings", None)
    if "settings" in snapshot and not isinstance(settings, dict):
        raise ValueError("agent_metadata.settings must be an object")
    if require_identity:
        for field in ("name", "version", "model"):
            text = snapshot.get(field)
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"evaluation agent_metadata requires {field}")
    return snapshot


def _decision_mode(config: EpisodeConfig) -> str:
    if config.realtime:
        return "realtime"
    if config.blocking_decisions:
        return "blocking"
    return "asynchronous"


def _game_versions(episodes: Sequence[Mapping[str, Any]]) -> list[Any]:
    found: list[Any] = []
    for row in episodes:
        versions = row.get("runtime_versions")
        version = versions.get("game_version") if isinstance(versions, Mapping) else None
        if version and version not in found:
            found.append(version)
    return found


def _batch_status(error: BaseException) -> str:
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        return "interrupted"
    return "failed"


class BenchmarkRunner:
    """Run isolated episodes, serial by default or in fresh spawn processes."""

    def __init__(
        self, *, backend_factory: Callable[[], Any] = lambda: "sharpy",
        record_dir: Optional[str | Path] = None,
    ) -> None:
        self.backend_factory = backend_factory
        self.record_dir = resolve_record_dir(record_dir)
        self.batch_directory: Optional[Path] = None
        self.batch_file: Optional[Path] = None
        self._episode_record_dir: Optional[Path] = None

    def run(
        self, configs: Sequence[EpisodeConfig] | BenchmarkSuite,
        agent_factory: Callable[[], Callable[[AgentInput], AgentTurn | Any]],
        *, max_decisions: Optional[int] = None,
        agent_metadata: Optional[Mapping[str, Any]] = None,
        max_parallel: int = 1,
    ) -> Dict[str, Any]:
        suite = configs if isinstance(configs, BenchmarkSuite) else None
        if suite is not None:
            if max_decisions is not None and max_decisions != suite.max_decisions:
                raise ValueError("Change Suite limits with with_overrides before running")
            plan = suite.episode_plan()
            configs = [item["config"] for item in plan]
            max_decisions = suite.max_decisions
        else:
            plan = None
            max_decisions = 200 if max_decisions is None else max_decisions
        if not configs or type(max_decisions) is not int or max_decisions < 1:
            raise ValueError("Provide at least one episode and a positive max_decisions")
        if any(not isinstance(config, EpisodeConfig) for config in configs):
            raise TypeError("Every benchmark configuration must be an EpisodeConfig")
        if type(max_parallel) is not int or max_parallel < 1:
            raise ValueError("max_parallel must be a positive integer")
        configs = list(configs)
        modes = {_decision_mode(config) for config in configs}
        if len(modes) != 1:
            raise ValueError("One batch cannot mix blocking, realtime and asynchronous episodes")
        decision_mode = modes.pop()
        require_identity = suite is not None and suite.to_dict().get("purpose") == "evaluation"
        frozen_agent_metadata = _snapshot_agent_metadata(
            agent_metadata, require_identity=require_identity,
        )
        payload = None
        if max_parallel > 1:
            from sc2bench_env.benchmark.parallel import serialize_factories
            payload = serialize_factories(self.backend_factory, agent_factory)
        batch_id = uuid4().hex
        started = datetime.now(timezone.utc)
        batch_dir = self.record_dir / f"batch_{started.strftime('%y%m%d_%H%M%S')}_{batch_id[:8]}"
        episode_dir = batch_dir / "episodes"
        episode_dir.mkdir(parents=True)
        self.batch_directory = batch_dir
        self.batch_file = batch_dir / "batch.json"
        self._episode_record_dir = episode_dir
        batch: Dict[str, Any] = {
            "schema_version": "0.4", "batch_id": batch_id,
            "started_at": started.isoformat(),
            "status": "running", "max_decisions": max_decisions,
            "agent_metadata": frozen_agent_metadata,
            "suite": ({"specification": suite.to_dict(), "sha256": suite.sha256,
                       "episode_count": len(configs)} if suite is not None else None),
            "platform_versions": collect_versions(),
            "platform_source_sha256": _platform_fingerprint(),
            "game_versions": [],
            "output_paths": {
                "record_dir": str(self.record_dir),
                "batch_dir": str(batch_dir),
                "batch_file": str(self.batch_file),
            },
            "planned_configs": [config.to_dict() for config in configs],
            "execution_policy": {
                "order": "case_major" if suite is not None else "supplied_order",
                "mode": "parallel" if max_parallel > 1 else "serial",
                "max_parallel": max_parallel,
                "process_per_episode": max_parallel > 1,
                "result_order": "planned_order",
                "fresh_agent_per_episode": True,
                "automatic_episode_reruns": False,
                "decision_mode": decision_mode,
                "time_limit_outcome": "tie",
                "decision_limit_status": "interrupted",
                "agent_call_failed_status": "interrupted",
                "backend_error_status": "failed",
            },
            "termination_counts": {},
            "episodes": [], "aggregate": Evaluator.summarize([]),
            "metrics": Evaluator.measure([]),
        }

        def refresh() -> None:
            batch["aggregate"] = Evaluator.summarize(batch["episodes"])
            batch["metrics"] = Evaluator.measure(batch["episodes"])
            batch["termination_counts"] = Evaluator.termination_counts(batch["episodes"])
            batch["game_versions"] = _game_versions(batch["episodes"])
            if suite is not None:
                batch["case_results"] = {
                    case["case_id"]: {
                        "aggregate": Evaluator.summarize(
                            row for row in batch["episodes"] if row.get("case_id") == case["case_id"]),
                        "metrics": Evaluator.measure(
                            row for row in batch["episodes"] if row.get("case_id") == case["case_id"]),
                        "termination_counts": Evaluator.termination_counts(
                            row for row in batch["episodes"] if row.get("case_id") == case["case_id"]),
                    }
                    for case in suite.to_dict()["cases"]
                }
            TrajectoryRecorder._write_atomic(self.batch_file, batch)

        def save_result(index: int, row: Dict[str, Any]) -> None:
            row["index"] = index
            row["decision_mode"] = decision_mode
            if plan is not None:
                row["case_id"] = plan[index - 1]["case_id"]
                row["repetition"] = plan[index - 1]["repetition"]
            batch["episodes"].append(row)
            batch["episodes"].sort(key=lambda item: item["index"])
            refresh()

        refresh()
        try:
            if payload is None:
                for index, config in enumerate(configs, start=1):
                    save_result(index, self._run_one(config, agent_factory, max_decisions))
            else:
                from sc2bench_env.benchmark.parallel import run_parallel
                run_parallel(configs, payload, episode_dir, max_decisions,
                             max_parallel, save_result)
        except BaseException as error:
            batch["status"] = _batch_status(error)
            batch["ended_at"] = datetime.now(timezone.utc).isoformat()
            batch["error_type"] = type(error).__name__
            refresh()
            raise
        batch["status"] = "completed"
        batch["ended_at"] = datetime.now(timezone.utc).isoformat()
        refresh()
        return batch

    def _run_one(
        self, config: EpisodeConfig,
        agent_factory: Callable[[], Callable[[AgentInput], AgentTurn | Any]],
        max_decisions: int,
        *, cancel_event: Any = None, record_callback: Optional[Callable[[Path], None]] = None,
    ) -> Dict[str, Any]:
        env: Optional[Environment] = None
        error_type = None
        record_directory = None
        runtime_versions = {"game_version": None}
        try:
            env = Environment(
                self.backend_factory(),
                record_dir=self._episode_record_dir or self.record_dir,
                on_record_started=record_callback,
            )
            observation = env.reset(config)
            record_directory = env.record_path
            runtime_versions = {"game_version": env.backend.snapshot().info.get("game_version")}
            agent = agent_factory()
            feedback = None
            for _ in range(max_decisions):
                if cancel_event is not None and cancel_event.is_set():
                    env.close(end_reason="caller_interrupted")
                    break
                tool_turn = env.begin_tool_turn()
                response = agent(AgentInput(
                    observation, feedback, tuple(env.tool_specs()), tool_turn.call,
                ))
                if cancel_event is not None and cancel_event.is_set():
                    tool_turn.abort()
                    env.close(end_reason="caller_interrupted")
                    break
                if not isinstance(response, AgentTurn):
                    env.record_protocol_error("agent_protocol_error")
                    continue
                turn = response
                if turn.stop_after_call_failures and not turn.call_failures:
                    raise ValueError("Cannot stop for a call failure without a failure record")
                game_ended_during_call = False
                for failure in turn.call_failures:
                    if not isinstance(failure, dict):
                        raise TypeError("call_failures must contain context dictionaries")
                    call_info = env.record_agent_call_failure(failure)
                    if call_info["terminated"]:
                        game_ended_during_call = True
                        break
                if game_ended_during_call:
                    tool_turn.abort()
                    break
                if turn.stop_after_call_failures:
                    env.record_agent_calls(turn.agent_context)
                    tool_turn.abort()
                    env.close(end_reason="agent_call_failed")
                    break
                try:
                    batch = tool_turn.finish()
                except ToolTurnError as error:
                    env.record_protocol_error(error.code, agent_context=turn.agent_context)
                    continue
                observation, feedback, terminated, _ = env.step(
                    batch, agent_context=turn.agent_context,
                )
                if terminated:
                    break
            else:
                env.close(end_reason="decision_limit")
        except AgentStopped as stop:
            if env is not None:
                try:
                    env.close(end_reason=stop.end_reason)
                except Exception as error:
                    error_type = type(error).__name__
        except Exception as error:
            error_type = type(error).__name__
            if env is not None and env.recorder is not None and env.recorder.summary is None:
                try:
                    env.close(end_reason="agent_error")
                except Exception:
                    pass
        finally:
            if env is not None:
                record_directory = env.record_path or record_directory
                try:
                    env.close()
                except Exception as error:
                    error_type = error_type or type(error).__name__

        if record_directory is None:
            return {
                "episode_id": None, "config": config.to_dict(), "status": "failed",
                "outcome": "unfinished", "result": None, "end_reason": "environment_creation_error",
                "game_time_seconds": None, "wall_time_seconds": None,
                "decision_count": None, "rejected_count": None,
                "record_directory": None, "error_type": error_type,
            }
        try:
            row = Evaluator.evaluate_episode(record_directory)
        except Exception as error:
            row = {
                "episode_id": None, "config": config.to_dict(), "status": "failed",
                "outcome": "unfinished", "result": None, "end_reason": "record_read_error",
                "game_time_seconds": None, "wall_time_seconds": None,
                "decision_count": None, "rejected_count": None,
            }
            error_type = error_type or type(error).__name__
        row["record_directory"] = str(record_directory)
        row["runtime_versions"] = runtime_versions
        if error_type is not None:
            row["error_type"] = error_type
        return row
