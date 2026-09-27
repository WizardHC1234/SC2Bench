"""Environment: reset / step / close, plus one tool turn per decision."""
from __future__ import annotations


import logging
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple
from uuid import uuid4

from sc2bench_env.backends.base import Backend
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.actions import (
    ActionValidationError,
    DecisionBatch,
    action_batch_to_dicts,
    attach_retry_ids,
)
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.races import require_supported_own_race
from sc2bench_env.interface.feedback import Feedback
from sc2bench_env.interface.observations import Observation
from sc2bench_env.recording.trajectory import TrajectoryRecorder
from sc2bench_env.paths import resolve_record_dir
from sc2bench_env.runtime.scheduler import Scheduler, trigger_from_advance
from sc2bench_env.runtime.task_manager import TaskManager

logger = logging.getLogger(__name__)


def _resolve_backend(backend: Optional[Backend | str]) -> Backend:
    if backend is None or backend == "fake":
        return FakeBackend()
    if isinstance(backend, Backend):
        return backend
    if backend == "sharpy":
        from sc2bench_env.backends.sharpy import SharpyBackend

        return SharpyBackend()
    raise ValueError(
        f"unknown backend {backend!r}; expected FakeBackend, SharpyBackend, 'fake', or 'sharpy'"
    )


class Environment:
    """Agent-facing environment. Does not expose Sharpy directly."""

    def __init__(
        self,
        backend: Optional[Backend | str] = None,
        *,
        record_trajectory: bool = True,
        record_dir: Optional[str | Path] = None,
        on_record_started: Optional[Any] = None,
    ) -> None:
        self.backend: Backend = _resolve_backend(backend)
        self.task_manager = TaskManager()
        self.scheduler = Scheduler()
        self.config: Optional[EpisodeConfig] = None
        self.recorder: Optional[TrajectoryRecorder] = None
        self._record_trajectory = record_trajectory
        self._record_dir = resolve_record_dir(record_dir)
        self._closed = True
        self._last_observation: Optional[dict[str, Any]] = None
        self._previous_observation: Optional[dict[str, Any]] = None
        self._last_feedback: Optional[dict[str, Any]] = None
        self.latest_observation: Optional[Observation] = None
        self.latest_feedback: Optional[Feedback] = None
        self._pending_release: Optional[dict[str, Any]] = None
        self._active_turn = None
        self._on_record_started = on_record_started

    @property
    def has_pending_decision(self) -> bool:
        return self._pending_release is not None

    def _platform_contract(self) -> str:
        from sc2bench_env.interface.platform_prompt import default_prompt_parts, render_prompt

        race = self.config.race if self.config is not None else "terran"
        return render_prompt(default_prompt_parts(race))

    def _retire_tool_turn(self) -> None:
        turn = self._active_turn
        self._active_turn = None
        if turn is not None:
            turn.retire()

    def reset(self, episode_config: Optional[EpisodeConfig] = None) -> Observation:
        config = episode_config if episode_config is not None else EpisodeConfig()
        if not isinstance(config, EpisodeConfig):
            raise TypeError("reset requires an EpisodeConfig")
        # Reject unsupported input before closing an existing game or recording
        # a misleading episode with a Terran catalog under another race name.
        require_supported_own_race(config.race)
        self._retire_tool_turn()
        self.close()
        self.recorder = None
        self._last_observation = None
        self._previous_observation = None
        self._last_feedback = None
        self.config = config
        self.task_manager.reset(race=config.race)
        self._closed = False

        if self._record_trajectory:
            self.recorder = TrajectoryRecorder(
                episode_id=uuid4().hex,
                config=self.config.to_dict(),
            )
        try:
            if self.recorder is not None:
                self.recorder.start(
                    self._record_dir, prompt=self._platform_contract(),
                    backend=type(self.backend).__name__,
                )
                if self._on_record_started is not None:
                    self._on_record_started(self.record_path)
            self.backend.set_replay_path(
                self.record_path / "replay.SC2Replay" if self.record_path is not None else None
            )
            snapshot = self.backend.start_episode(self.config)
            observation = self._build_observation(snapshot)
            if self.recorder is not None:
                self.recorder.record_reset(observation.to_dict())
        except BaseException as exc:
            self._record_failure(exc, phase="reset")
            raise
        self._remember_observation(observation, keep_previous=False)
        observation.text_previous = self._previous_observation
        self.latest_observation = observation
        return observation

    def open_player(
        self, episode_config: EpisodeConfig, snapshot, *, folder_name: str,
    ) -> Observation:
        """Attach to a game VersusMatch already started. Does not launch a backend."""
        if not isinstance(episode_config, EpisodeConfig):
            raise TypeError("open_player requires an EpisodeConfig")
        require_supported_own_race(episode_config.race)
        if not self._closed:
            raise RuntimeError("Environment is already open")
        self.config = episode_config
        self.task_manager.reset(race=episode_config.race)
        self._closed = False
        self._pending_release = None
        self.latest_feedback = None
        self._last_feedback = None
        self._previous_observation = None
        recorded = episode_config.to_dict()
        recorded["opponent"] = "agent"
        recorded["player_name"] = folder_name
        if self._record_trajectory:
            self.recorder = TrajectoryRecorder(episode_id=uuid4().hex, config=recorded)
            self.recorder.start(
                self._record_dir, prompt=self._platform_contract(),
                backend="versus", folder_name=folder_name,
            )
        observation = self._build_observation(snapshot)
        if self.recorder is not None:
            self.recorder.record_reset(observation.to_dict())
        self._remember_observation(observation, keep_previous=False)
        observation.text_previous = self._previous_observation
        self.latest_observation = observation
        return observation

    def release_decision(
        self,
        decision: DecisionBatch,
        *,
        agent_context: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Submit one side's decision and let that bot run. Return True if it stayed paused."""
        if self._closed or self.config is None:
            raise RuntimeError("Environment is closed; call reset() first")
        if not isinstance(decision, DecisionBatch):
            raise TypeError("release_decision requires a DecisionBatch")
        if self._pending_release is not None:
            raise RuntimeError("This side already has a decision in progress")
        started = time.perf_counter()
        batch = decision
        submitted = list(batch.raw)
        snapshot = self.backend.snapshot()
        before = snapshot.game_time_seconds
        if snapshot.terminated:
            self._finish_terminal_snapshot(
                snapshot, submitted=submitted, agent_context=agent_context, started=started, before=before,
            )
            return True
        receipts, trigger = self._accept_batch(batch, snapshot)
        self._retire_tool_turn()
        self._pending_release = {
            "started": started, "before": before, "submitted": submitted, "batch": batch,
            "receipts": receipts, "agent_context": agent_context,
        }
        release = getattr(self.backend, "release", None)
        if not callable(release):
            raise RuntimeError("This backend cannot release one versus side")
        release(trigger)
        return False

    def complete_pending(self) -> Optional[Tuple[Observation, Feedback, bool, dict[str, Any]]]:
        """Record the observation for a decision whose bot has paused or ended."""
        pending = self._pending_release
        if pending is None or self.config is None:
            return None
        self._pending_release = None
        updates = self.backend.collect_updates()
        snapshot = self.backend.snapshot()
        self.task_manager.apply_updates(updates, game_time=snapshot.game_time_seconds)
        observation = self._build_observation(snapshot)
        batch = pending["batch"]
        feedback = Feedback(
            receipts=pending["receipts"],
            events=list(self.task_manager.recent_events[-8:]),
            name_normalizations=list(batch.normalizations),
        )
        terminated = bool(snapshot.terminated)
        info = {
            "result": snapshot.result,
            "end_reason": self._episode_end_reason(snapshot) if terminated else None,
            "backend": snapshot.info.get("backend"),
            "active_demands": len(self.task_manager.active_demands()),
        }
        if self.recorder is not None:
            self.recorder.record_step(
                actions=action_batch_to_dicts(batch),
                submitted_decision=pending["submitted"],
                observation=observation.to_dict(), feedback=feedback.to_dict(),
                terminated=terminated, info=info,
                game_time_before_seconds=pending["before"],
                wall_time_seconds=time.perf_counter() - pending["started"],
                agent_context=pending["agent_context"],
            )
            if terminated:
                self._record_episode_end(snapshot)
        self._remember_observation(observation)
        observation.text_previous = self._previous_observation
        self._last_feedback = feedback.to_dict()
        self.latest_observation = observation
        self.latest_feedback = feedback
        return observation, feedback, terminated, info

    def _record_rejected_decision(
        self, exc: ActionValidationError, snapshot, *, submitted, agent_context, started, before,
    ) -> None:
        feedback = Feedback(receipts=[], events=[{"type": "decision_rejected", "reason": str(exc)}])
        observation = self._build_observation(snapshot)
        info = {
            "error": str(exc), "backend": snapshot.info.get("backend"),
            "result": snapshot.result,
            "end_reason": self._episode_end_reason(snapshot) if snapshot.terminated else None,
        }
        if self.recorder is not None:
            self.recorder.record_step(
                actions=[], submitted_decision=submitted, accepted=False,
                validation_error=str(exc), observation=observation.to_dict(),
                feedback=feedback.to_dict(), terminated=snapshot.terminated, info=info,
                game_time_before_seconds=before,
                wall_time_seconds=time.perf_counter() - started, agent_context=agent_context,
            )
            if snapshot.terminated:
                self._record_episode_end(snapshot)
        self._remember_observation(observation)
        observation.text_previous = self._previous_observation
        self._last_feedback = feedback.to_dict()
        self.latest_observation = observation
        self.latest_feedback = feedback
        return observation, feedback, snapshot.terminated, info

    def _finish_terminal_snapshot(
        self, snapshot, *, submitted, agent_context, started, before,
    ) -> None:
        self.task_manager.apply_updates(self.backend.collect_updates(), game_time=before)
        observation = self._build_observation(snapshot)
        feedback = Feedback(events=[{
            "type": "episode_ended", "reason": self._episode_end_reason(snapshot),
        }])
        info = {
            "result": snapshot.result, "end_reason": self._episode_end_reason(snapshot),
            "backend": snapshot.info.get("backend"), "decision_applied": False,
            "active_demands": len(self.task_manager.active_demands()),
        }
        if self.recorder is not None:
            self.recorder.record_step(
                actions=[], submitted_decision=submitted, observation=observation.to_dict(),
                feedback=feedback.to_dict(), terminated=True, info=info,
                game_time_before_seconds=before,
                wall_time_seconds=time.perf_counter() - started, agent_context=agent_context,
            )
            self._record_episode_end(snapshot)
        self._remember_observation(observation)
        observation.text_previous = self._previous_observation
        self._last_feedback = feedback.to_dict()
        self.latest_observation = observation
        self.latest_feedback = feedback

    def tool_specs(self) -> tuple[ToolSpec, ...]:
        from sc2bench_env.interface.tools import tool_specs
        race = self.config.race if self.config is not None else "terran"
        return tool_specs(race=race)

    def begin_tool_turn(self):
        from sc2bench_env.runtime.tool_turn import ToolTurn, ToolTurnError

        if self._last_observation is None or self.config is None:
            raise RuntimeError("Call reset() before using tools")
        if self._active_turn is not None and self._active_turn.active:
            raise ToolTurnError("turn_in_progress")
        snapshot = self.backend.snapshot()
        decision_index = 0
        if self.recorder is not None:
            decision_index = self.recorder.decision_count

        def record(**payload: Any) -> None:
            if self.recorder is None:
                return
            self.recorder.record_tool_event(**payload)

        turn = ToolTurn(
            specs=self.tool_specs(),
            race=self.config.race,
            observation=self._last_observation,
            game_time_seconds=snapshot.game_time_seconds,
            decision_index=decision_index,
            record=record,
        )
        self._active_turn = turn
        return turn

    def _accept_batch(self, batch: DecisionBatch, snapshot):
        assert self.config is not None
        baselines = {
            action.identity(): snapshot.owned_count(action.action, action.target or "")
            for action in batch.actions
            if action.action in {"build", "train", "research", "scan"}
        }
        upgrades = set(snapshot.info.get("upgrades") or [])
        researching_raw = snapshot.info.get("in_progress_research") or []
        if isinstance(researching_raw, dict):
            researching = {key for key, value in researching_raw.items() if value}
        else:
            researching = set(researching_raw)
        receipts = self.task_manager.submit_decision(
            batch, game_time=snapshot.game_time_seconds, baseline_owned=baselines,
            known_upgrades=upgrades, researching=researching,
            idle_army=self._idle_army_counts(snapshot),
            bunker_garrison=snapshot.info.get("bunker_garrison"),
        )
        self.backend.submit(self.task_manager.active_demands())
        trigger = trigger_from_advance(
            batch.advance, max_game_time_seconds=self.config.game_time_limit_seconds,
        )
        return receipts, trigger

    def record_protocol_error(self, code: str, *, agent_context: Optional[dict[str, Any]] = None) -> None:
        turn = self._active_turn
        if turn is not None:
            turn.abort()
        self._active_turn = None
        if self.recorder is None:
            return
        snapshot = self.backend.snapshot()
        self.recorder.record_protocol_error(
            code=code, agent_context=agent_context,
            game_time_seconds=snapshot.game_time_seconds,
        )

    def record_agent_call_failure(self, agent_context: dict[str, Any]) -> dict[str, Any]:
        """Record an external harness failure without applying actions or waiting."""
        if self._closed or self.config is None:
            raise RuntimeError("Environment is closed; call reset() first")
        snapshot = self.backend.snapshot()
        if self.recorder is not None:
            self.recorder.record_agent_call_failure(
                agent_context, game_time=snapshot.game_time_seconds,
            )
        # Continuous mode may have progressed or ended during inference.
        self.task_manager.apply_updates(self.backend.collect_updates(),
                                        game_time=snapshot.game_time_seconds)
        observation = self._remember_observation(self._build_observation(snapshot))
        if snapshot.terminated:
            self._record_episode_end(snapshot, observation=observation)
        return {"terminated": snapshot.terminated, "result": snapshot.result,
                "end_reason": self._episode_end_reason(snapshot) if snapshot.terminated else None}

    def step(
        self,
        decision: DecisionBatch,
        *,
        retry_ids: Sequence[Optional[str]] | None = None,
        agent_context: Optional[dict[str, Any]] = None,
    ) -> Tuple[Observation, Feedback, bool, dict[str, Any]]:
        if decision is None:
            return self._ended_without_decision(agent_context)
        if not isinstance(decision, DecisionBatch):
            raise TypeError("Environment.step requires a DecisionBatch")
        if self._closed or self.config is None:
            raise RuntimeError("Environment is closed; call reset() first")
        if self.recorder is not None and self.recorder.summary is not None:
            raise RuntimeError("Episode ended; call reset() before submitting another decision")
        try:
            return self._step(decision, retry_ids=retry_ids, agent_context=agent_context)
        except BaseException as exc:
            self._record_failure(
                exc, phase="step", submitted_decision=list(decision.raw),
                agent_context=agent_context,
            )
            raise

    def _record_failure(
        self, error: BaseException, *, phase: str, submitted_decision: Any = None,
        agent_context: Optional[dict[str, Any]] = None,
    ) -> None:
        try:
            if self.recorder is not None:
                self.recorder.record_error(
                    error, phase=phase, submitted_decision=submitted_decision,
                    agent_context=agent_context,
                )
                interrupted = isinstance(error, (KeyboardInterrupt, SystemExit))
                self.recorder.finalize(
                    status="interrupted" if interrupted else "failed",
                    end_reason="caller_interrupted" if interrupted else f"{phase}_error",
                    error=str(error),
                )
        except BaseException:
            logger.exception("Could not persist episode failure")
        finally:
            try:
                self.backend.close_episode()
            except BaseException:
                # Cleanup must not replace the original startup/step failure.
                pass
            self._closed = True
            self._stop_console_log()

    def _ended_without_decision(self, agent_context: Optional[dict[str, Any]]):
        """The game ended while the agent was thinking, so there is no batch to apply."""
        if self._closed or self.config is None:
            raise RuntimeError("Environment is closed; call reset() first")
        snapshot = self.backend.snapshot()
        if not snapshot.terminated:
            raise TypeError("Environment.step requires a DecisionBatch")
        started = time.perf_counter()
        before = snapshot.game_time_seconds
        self.task_manager.apply_updates(self.backend.collect_updates(), game_time=before)
        observation = self._build_observation(snapshot)
        feedback = Feedback(events=[{
            "type": "episode_ended", "reason": self._episode_end_reason(snapshot),
        }])
        info = {
            "result": snapshot.result, "end_reason": self._episode_end_reason(snapshot),
            "backend": snapshot.info.get("backend"), "decision_applied": False,
            "active_demands": len(self.task_manager.active_demands()),
        }
        if self.recorder is not None:
            self.recorder.record_step(
                actions=[], submitted_decision=None, observation=observation.to_dict(),
                feedback=feedback.to_dict(), terminated=True, info=info,
                game_time_before_seconds=before,
                wall_time_seconds=time.perf_counter() - started, agent_context=agent_context,
            )
            self._record_episode_end(snapshot)
        self._remember_observation(observation)
        observation.text_previous = self._previous_observation
        self._last_feedback = feedback.to_dict()
        self.latest_observation = observation
        self.latest_feedback = feedback
        return observation, feedback, True, info

    def _step(
        self, decision: DecisionBatch,
        *, retry_ids: Sequence[Optional[str]] | None,
        agent_context: Optional[dict[str, Any]],
    ) -> Tuple[Observation, Feedback, bool, dict[str, Any]]:
        assert self.config is not None
        started = time.perf_counter()
        submitted = list(decision.raw)

        snapshot = self.backend.snapshot()
        before = snapshot.game_time_seconds
        if snapshot.terminated:
            # In continuous mode the game may finish while the Agent is thinking.
            # Return the ending without registering its now-stale new commands.
            self.task_manager.apply_updates(self.backend.collect_updates(), game_time=before)
            observation = self._build_observation(snapshot)
            feedback = Feedback(events=[{
                "type": "episode_ended", "reason": self._episode_end_reason(snapshot),
            }])
            info = {
                "result": snapshot.result, "end_reason": self._episode_end_reason(snapshot),
                "backend": snapshot.info.get("backend"), "decision_applied": False,
                "active_demands": len(self.task_manager.active_demands()),
            }
            if self.recorder is not None:
                self.recorder.record_step(
                    actions=[], submitted_decision=submitted,
                    observation=observation.to_dict(), feedback=feedback.to_dict(),
                    terminated=True, info=info, game_time_before_seconds=before,
                    wall_time_seconds=time.perf_counter() - started, agent_context=agent_context,
                )
                self._record_episode_end(snapshot)
            self._remember_observation(observation)
            observation.text_previous = self._previous_observation
            self._last_feedback = feedback.to_dict()
            self.latest_observation = observation
            self.latest_feedback = feedback
            return observation, feedback, True, info
        try:
            batch = attach_retry_ids(decision, retry_ids)
        except ActionValidationError as exc:
            snapshot = self.backend.snapshot()
            feedback = Feedback(
                receipts=[],
                events=[{"type": "decision_rejected", "reason": str(exc)}],
            )
            observation = self._build_observation(snapshot)
            info = {"error": str(exc), "backend": snapshot.info.get("backend"),
                    "result": snapshot.result,
                    "end_reason": self._episode_end_reason(snapshot) if snapshot.terminated else None}
            if self.recorder is not None:
                self.recorder.record_step(
                    actions=[], submitted_decision=submitted,
                    accepted=False, validation_error=str(exc),
                    observation=observation.to_dict(), feedback=feedback.to_dict(),
                    terminated=snapshot.terminated, info=info,
                    game_time_before_seconds=before,
                    wall_time_seconds=time.perf_counter() - started,
                    agent_context=agent_context,
                )
                if snapshot.terminated:
                    self._record_episode_end(snapshot)
            self._remember_observation(observation)
            observation.text_previous = self._previous_observation
            self._last_feedback = feedback.to_dict()
            self.latest_observation = observation
            self.latest_feedback = feedback
            return observation, feedback, snapshot.terminated, info

        receipts, trigger = self._accept_batch(batch, snapshot)
        self._retire_tool_turn()
        terminated = self.scheduler.run_until_next_decision(self.backend, trigger)

        updates = self.backend.collect_updates()
        snapshot = self.backend.snapshot()
        self.task_manager.apply_updates(updates, game_time=snapshot.game_time_seconds)

        observation = self._build_observation(snapshot)
        # Receipts refer to submission; events refer to the returned boundary,
        # including completions/failures that occurred while this step waited.
        feedback = Feedback(
            receipts=receipts,
            events=list(self.task_manager.recent_events[-8:]),
            name_normalizations=list(batch.normalizations),
        )
        info = {
            "result": snapshot.result,
            "end_reason": self._episode_end_reason(snapshot) if snapshot.terminated else None,
            "backend": snapshot.info.get("backend"),
            "active_demands": len(self.task_manager.active_demands()),
        }

        if self.recorder is not None:
            self.recorder.record_step(
                actions=action_batch_to_dicts(batch),
                submitted_decision=submitted,
                observation=observation.to_dict(),
                feedback=feedback.to_dict(),
                terminated=terminated or snapshot.terminated,
                info=info,
                game_time_before_seconds=before,
                wall_time_seconds=time.perf_counter() - started,
                agent_context=agent_context,
            )
            if terminated or snapshot.terminated:
                self._record_episode_end(snapshot)

        self._remember_observation(observation)
        observation.text_previous = self._previous_observation
        self._last_feedback = feedback.to_dict()
        self.latest_observation = observation
        self.latest_feedback = feedback
        return observation, feedback, bool(terminated or snapshot.terminated), info

    @staticmethod
    def _episode_end_reason(snapshot) -> str:
        if snapshot.result and snapshot.result.startswith("error:"):
            return "backend_error"
        if snapshot.end_reason:
            return snapshot.end_reason
        # Compatibility for custom/Fake backends lacking explicit attribution.
        # Do not classify natural Victory/Defeat/Tie by proximity to a deadline.
        return "time_limit" if snapshot.result == "timeout" else "game_ended"

    def _record_episode_end(self, snapshot, *, observation=None) -> None:
        if self.recorder is None:
            return
        reason = self._episode_end_reason(snapshot)
        status = ("failed" if reason == "backend_error" else
                  "interrupted" if reason == "closed_by_caller" else "completed")
        self.recorder.finalize(
            None if status == "failed" else snapshot.result,
            status=status, end_reason=reason,
            error=snapshot.result if status == "failed" else None,
            observation=observation,
        )

    def close(self, *, end_reason: str = "closed_by_caller") -> None:
        self._retire_tool_turn()
        if self._closed:
            return
        try:
            if self.recorder is not None and self.recorder.summary is None:
                snapshot = self.backend.snapshot()
                if snapshot.terminated:
                    self.task_manager.apply_updates(
                        self.backend.collect_updates(), game_time=snapshot.game_time_seconds,
                    )
                    observation = self._remember_observation(self._build_observation(snapshot))
                    self._record_episode_end(snapshot, observation=observation)
            self.backend.close_episode()
        except BaseException as exc:
            try:
                if self.recorder is not None:
                    self.recorder.record_error(exc, phase="close")
                    self.recorder.finalize(status="failed", end_reason="close_error", error=str(exc))
            except BaseException:
                logger.exception("Could not persist cleanup failure")
            raise
        else:
            if self.recorder is not None:
                self.recorder.finalize(status="interrupted", end_reason=end_reason)
        finally:
            self._closed = True
            self._stop_console_log()

    def _stop_console_log(self) -> None:
        if self.recorder is not None:
            self.recorder.stop_console_log()

    @property
    def record_path(self) -> Optional[Path]:
        """Episode artifacts directory; never part of the model Observation."""
        return self.recorder.directory if self.recorder is not None else None

    def trajectory(self) -> Optional[dict[str, Any]]:
        if self.recorder is None:
            return None
        return self.recorder.to_dict()

    def _remember_observation(self, observation, *, keep_previous: bool = True) -> dict[str, Any]:
        payload = observation if isinstance(observation, dict) else observation.to_dict()
        if keep_previous:
            self._previous_observation = self._last_observation
        else:
            self._previous_observation = None
        self._last_observation = payload
        return payload


    def _build_observation(self, snapshot) -> Observation:
        from sc2bench_env.runtime.observation_builder import build_observation
        return build_observation(snapshot, self.task_manager, self.config, self._previous_observation)

    def _idle_army_counts(self, snapshot) -> dict[str, int]:
        from sc2bench_env.runtime.observation_builder import idle_army_counts
        return idle_army_counts(snapshot, self.task_manager)
