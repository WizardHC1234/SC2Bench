"""Serial episodes for external agents; no built-in policy or model calls."""

from __future__ import annotations

import json
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence
from uuid import uuid4

from sc2bench_env.benchmark.evaluator import Evaluator
from sc2bench_env.benchmark.suite import BenchmarkSuite
from sc2bench_env.env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.feedback import Feedback
from sc2bench_env.interface.observations import Observation
from sc2bench_env.recording.trajectory import TrajectoryRecorder, _versions
from sc2bench_env.paths import record_reference, resolve_record_dir, resolve_results_dir


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


@dataclass(frozen=True)
class AgentInput:
    observation: Observation
    feedback: Optional[Feedback]
    platform_messages: list[dict[str, str]]

class AgentStopped(RuntimeError):
    """An external Agent's deliberate stop, distinct from an unexpected error."""

    def __init__(self, end_reason: str = "agent_stopped") -> None:
        if end_reason not in {"agent_stopped", "invalid_decision_limit"}:
            raise ValueError("Unsupported Agent stop reason")
        super().__init__(end_reason)
        self.end_reason = end_reason


@dataclass(frozen=True)
class AgentTurn:
    decision: Any
    # Only messages/output supplied by the external Agent are actual interactions.
    agent_context: Optional[dict[str, Any]] = None
    # Failed external calls are recorded without submitting an environment action.
    call_failures: tuple[dict[str, Any], ...] = ()
    stop_after_call_failures: bool = False


def _snapshot_agent_metadata(value: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("agent_metadata must be a mapping")
    try:
        snapshot = json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError("agent_metadata must contain JSON-compatible values") from error
    forbidden = {"api_key", "access_token", "authorization", "password",
                 "secret", "client_secret", "api_base_url"}

    def check_keys(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key.lower() in forbidden:
                    raise ValueError("agent_metadata must not contain credentials or endpoint URLs")
                check_keys(child)
        elif isinstance(item, list):
            for child in item:
                check_keys(child)

    check_keys(snapshot)
    return snapshot


class BenchmarkRunner:
    """Run a list of configurations sequentially, isolating each episode."""

    def __init__(
        self, *, backend_factory: Callable[[], Any] = lambda: "sharpy",
        record_dir: Optional[str | Path] = None,
        results_dir: Optional[str | Path] = None,
    ) -> None:
        self.backend_factory = backend_factory
        self.record_dir = resolve_record_dir(record_dir)
        self.results_dir = resolve_results_dir(results_dir, record_dir=self.record_dir)

    def run(
        self, configs: Sequence[EpisodeConfig] | BenchmarkSuite,
        agent_factory: Callable[[], Callable[[AgentInput], AgentTurn | Any]],
        *, max_decisions: Optional[int] = None,
        agent_metadata: Optional[Mapping[str, Any]] = None,
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
        frozen_agent_metadata = _snapshot_agent_metadata(agent_metadata)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        batch_id = uuid4().hex
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = self.results_dir / f"run_{stamp}_{batch_id}.json"
        batch: Dict[str, Any] = {
            "schema_version": "0.3", "batch_id": batch_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "running", "max_decisions": max_decisions,
            "agent_metadata": frozen_agent_metadata,
            "suite": ({"specification": suite.to_dict(), "sha256": suite.sha256,
                       "episode_count": len(configs)} if suite is not None else None),
            "platform_versions": _versions(),
            "platform_source_sha256": _platform_fingerprint(),
            "output_paths": {"record_dir": str(self.record_dir), "results_dir": str(self.results_dir)},
            "planned_configs": [config.to_dict() for config in configs],
            "execution_policy": {
                "order": "case_major" if suite is not None else "supplied_order",
                "fresh_agent_per_episode": True,
                "automatic_episode_reruns": False,
                "time_limit_outcome": "tie",
                "decision_limit_status": "interrupted",
                "agent_call_failed_status": "interrupted",
                "backend_error_status": "failed",
            },
            "termination_counts": {},
            "episodes": [], "aggregate": Evaluator.summarize([]),
        }
        TrajectoryRecorder._write_atomic(output_path, batch)
        for index, config in enumerate(configs, start=1):
            row = self._run_one(config, agent_factory, max_decisions)
            row["index"] = index
            if plan is not None:
                row["case_id"] = plan[index - 1]["case_id"]
                row["repetition"] = plan[index - 1]["repetition"]
            if row.get("record_directory") is not None:
                row["record_directory"] = record_reference(row["record_directory"], self.results_dir)
            batch["episodes"].append(row)
            batch["aggregate"] = Evaluator.summarize(batch["episodes"])
            batch["termination_counts"] = Evaluator.termination_counts(batch["episodes"])
            if suite is not None:
                batch["case_results"] = {
                    case["case_id"]: {
                        "aggregate": Evaluator.summarize(
                            row for row in batch["episodes"] if row["case_id"] == case["case_id"]),
                        "termination_counts": Evaluator.termination_counts(
                            row for row in batch["episodes"] if row["case_id"] == case["case_id"]),
                    }
                    for case in suite.to_dict()["cases"]
                }
            TrajectoryRecorder._write_atomic(output_path, batch)
        batch["status"] = "completed"
        batch["ended_at"] = datetime.now(timezone.utc).isoformat()
        TrajectoryRecorder._write_atomic(output_path, batch)
        return {**batch, "summary_path": str(output_path)}

    def _run_one(
        self, config: EpisodeConfig,
        agent_factory: Callable[[], Callable[[AgentInput], AgentTurn | Any]],
        max_decisions: int,
    ) -> Dict[str, Any]:
        env: Optional[Environment] = None
        error_type = None
        record_directory = None
        runtime_versions = {"game_version": None}
        try:
            env = Environment(self.backend_factory(), record_dir=self.record_dir)
            observation = env.reset(config)
            record_directory = env.record_path
            runtime_versions = {"game_version": env.backend.snapshot().info.get("game_version")}
            agent = agent_factory()
            feedback = None
            for _ in range(max_decisions):
                response = agent(AgentInput(observation, feedback, env.get_context()))
                turn = response if isinstance(response, AgentTurn) else AgentTurn(response)
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
                    break
                if turn.stop_after_call_failures:
                    env.close(end_reason="agent_call_failed")
                    break
                observation, feedback, terminated, _ = env.step(
                    turn.decision, agent_context=turn.agent_context,
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
