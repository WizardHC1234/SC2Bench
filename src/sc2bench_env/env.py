"""Environment: get_system_prompt / reset / step / close for external agents."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple
from uuid import uuid4

from sc2bench_env.backends.base import Backend
from sc2bench_env.backends.fake import FakeBackend
from sc2bench_env.interface.action_catalog import render_system_prompt
from sc2bench_env.interface.actions import (
    ActionValidationError,
    DecisionBatch,
    action_batch_to_dicts,
    attach_retry_ids,
    parse_decision,
)
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.races import require_supported_own_race
from sc2bench_env.interface.race_views import build_race_view
from sc2bench_env.interface.briefing import available_targets, relevant_zone_ids
from sc2bench_env.interface.feedback import Feedback
from sc2bench_env.interface.observations import (
    EconomyView,
    GameView,
    MapControlView,
    Observation,
    ResourcesView,
    split_own_forces,
)
from sc2bench_env.recording.trajectory import TrajectoryRecorder
from sc2bench_env.paths import resolve_record_dir
from sc2bench_env.recording.context import platform_messages
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
        # Internal Runner hook: index the artifact before slow game startup.
        self._record_started_callback = None

    def get_system_prompt(self) -> str:
        race = "terran"
        if self.config is not None:
            race = self.config.race
        return render_system_prompt(race=race)

    def reset(self, episode_config: Optional[EpisodeConfig] = None) -> Observation:
        config = episode_config if episode_config is not None else EpisodeConfig()
        if not isinstance(config, EpisodeConfig):
            raise TypeError("reset requires an EpisodeConfig")
        # Reject unsupported input before closing an existing game or recording
        # a misleading episode with a Terran catalog under another race name.
        require_supported_own_race(config.race)
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
                    self._record_dir, prompt=self.get_system_prompt(),
                    backend=type(self.backend).__name__,
                )
                if self._record_started_callback is not None:
                    self._record_started_callback(self.record_path)
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
                self._record_dir, prompt=self.get_system_prompt(),
                backend="versus", folder_name=folder_name,
            )
        observation = self._build_observation(snapshot)
        if self.recorder is not None:
            self.recorder.record_reset(observation.to_dict())
        self._remember_observation(observation, keep_previous=False)
        self.latest_observation = observation
        return observation

    def release_decision(
        self,
        decision_json: Sequence[dict[str, Any]] | DecisionBatch | None,
        *,
        agent_context: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Submit one side's decision and let that bot run. Return True if it stayed paused."""
        if self._closed or self.config is None:
            raise RuntimeError("Environment is closed; call reset() first")
        if self._pending_release is not None:
            raise RuntimeError("This side already has a decision in progress")
        started = time.perf_counter()
        submitted = list(decision_json.raw) if isinstance(decision_json, DecisionBatch) else decision_json
        snapshot = self.backend.snapshot()
        before = snapshot.game_time_seconds
        if snapshot.terminated:
            self._finish_terminal_snapshot(
                snapshot, submitted=submitted, agent_context=agent_context, started=started, before=before,
            )
            return True
        try:
            batch = (decision_json if isinstance(decision_json, DecisionBatch)
                     else parse_decision(decision_json, race=self.config.race))
        except ActionValidationError as exc:
            self._record_rejected_decision(
                exc, snapshot, submitted=submitted, agent_context=agent_context,
                started=started, before=before,
            )
            return True
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
        self._last_feedback = feedback.to_dict()
        self.latest_observation = observation
        self.latest_feedback = feedback

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
        self._last_feedback = feedback.to_dict()
        self.latest_observation = observation
        self.latest_feedback = feedback

    def get_context(self) -> list[dict[str, str]]:
        """Optional platform message template; external agents may customize it."""
        if self._last_observation is None:
            raise RuntimeError("Call reset() before requesting context")
        return platform_messages(
            self.get_system_prompt(), self._last_observation, self._last_feedback,
            previous=self._previous_observation,
        )

    def tool_schemas(self) -> list[dict[str, Any]]:
        from sc2bench_env.interface.tools import tool_schemas
        race = self.config.race if self.config is not None else "terran"
        return tool_schemas(race=race)

    def call_tool(self, name: str, arguments: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        from sc2bench_env.interface.tools import execute_tool
        if self._last_observation is None or self.config is None:
            raise RuntimeError("Call reset() before using tools")
        return execute_tool(
            name, arguments, observation=self._last_observation, race=self.config.race,
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
        decision_json: Sequence[dict[str, Any]] | DecisionBatch | None = None,
        *,
        retry_ids: Sequence[Optional[str]] | None = None,
        agent_context: Optional[dict[str, Any]] = None,
    ) -> Tuple[Observation, Feedback, bool, dict[str, Any]]:
        if self._closed or self.config is None:
            raise RuntimeError("Environment is closed; call reset() first")
        if self.recorder is not None and self.recorder.summary is not None:
            raise RuntimeError("Episode ended; call reset() before submitting another decision")
        try:
            return self._step(decision_json, retry_ids=retry_ids, agent_context=agent_context)
        except BaseException as exc:
            submitted = list(decision_json.raw) if isinstance(decision_json, DecisionBatch) else decision_json
            self._record_failure(
                exc, phase="step", submitted_decision=submitted,
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

    def _step(
        self, decision_json: Sequence[dict[str, Any]] | DecisionBatch | None,
        *, retry_ids: Sequence[Optional[str]] | None,
        agent_context: Optional[dict[str, Any]],
    ) -> Tuple[Observation, Feedback, bool, dict[str, Any]]:
        assert self.config is not None
        started = time.perf_counter()
        submitted = list(decision_json.raw) if isinstance(decision_json, DecisionBatch) else decision_json

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
            self._last_feedback = feedback.to_dict()
            return observation, feedback, True, info
        try:
            batch = (
                decision_json
                if isinstance(decision_json, DecisionBatch)
                else parse_decision(decision_json, race=self.config.race)
            )
            batch = attach_retry_ids(batch, retry_ids)
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
            self._last_feedback = feedback.to_dict()
            return observation, feedback, snapshot.terminated, info

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
            batch,
            game_time=snapshot.game_time_seconds,
            baseline_owned=baselines,
            known_upgrades=upgrades,
            researching=researching,
            idle_army=self._idle_army_counts(snapshot),
            bunker_garrison=snapshot.info.get("bunker_garrison"),
        )
        self.backend.submit(self.task_manager.active_demands())
        trigger = trigger_from_advance(
            batch.advance,
            max_game_time_seconds=self.config.game_time_limit_seconds,
        )
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
        self._last_feedback = feedback.to_dict()
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
        assert self.config is not None
        under = dict(snapshot.info.get("under_construction") or {})
        in_prod = dict(snapshot.info.get("in_production_units") or {})
        in_research = snapshot.info.get("in_progress_research") or []
        if isinstance(in_research, dict):
            in_research = [key for key, value in in_research.items() if value]
        else:
            in_research = list(in_research)
        upgrades = list(snapshot.info.get("upgrades") or [])
        building = self.task_manager.building_summary(snapshot.buildings, under)
        training = self.task_manager.training_summary(in_prod)
        research = self.task_manager.research_summary(upgrades, in_research)
        scout_demand = next(
            (d for d in self.task_manager.active_demands() if d.action == "scout"),
            None,
        )
        race_view = build_race_view(
            race=self.config.race, snapshot=snapshot, scout_demand=scout_demand,
        )
        combat = self._combat_summary(snapshot)
        limit = self.config.game_time_limit_seconds
        remaining = None
        if limit is not None:
            remaining = max(0.0, float(limit) - float(snapshot.game_time_seconds))
        # Count living permanent workers from the same entity inventory as
        # Own Forces, including loaded workers. Food-workers is not inventory.
        worker_count = sum(int(snapshot.units.get(name, 0)) for name in ("scv", "probe", "drone"))
        army_supply = int(
            snapshot.info.get(
                "supply_army",
                max(0, int(snapshot.supply_used) - worker_count),
            )
        )
        own_base_count = int(snapshot.info.get("base_count", sum(
            row.get("known_owner") == "self"
            for row in snapshot.info.get("zone_state") or []
        )))
        zones = list(snapshot.info.get("zones") or [])
        zone_state = list(snapshot.info.get("zone_state") or [])
        known_enemy = int(snapshot.info.get("known_enemy_base_count", 0))
        map_own_base_count = int(snapshot.info.get("own_base_count", own_base_count))
        unconfirmed = int(
            snapshot.info.get(
                "unconfirmed_expansion_count",
                max(0, len(zones) - map_own_base_count - known_enemy),
            )
        )
        observed_race = snapshot.info.get("identified_enemy_race")
        enemy_race = (observed_race if observed_race in {"terran", "protoss", "zerg"}
                      else "unknown" if self.config.enemy_race == "random" else self.config.enemy_race)
        resources = ResourcesView(
            minerals=snapshot.minerals,
            vespene=snapshot.vespene,
            supply_used=snapshot.supply_used,
            supply_cap=snapshot.supply_cap,
        )
        topology = dict(snapshot.info.get("map_topology") or {})
        relevant = relevant_zone_ids(
            {"zone_state": zone_state, "map_topology": topology, "combat": combat,
             "scouting": race_view.scouting},
            self._previous_observation,
        )
        targets = available_targets(
            race=self.config.race, building=building,
            structures=list(snapshot.info.get("structures") or []),
            research=research,
            units=dict(snapshot.units),
        )
        return Observation(
            game_time_seconds=snapshot.game_time_seconds,
            race=self.config.race,
            enemy_race=enemy_race,
            resources=resources,
            game=GameView(
                game_time_seconds=float(snapshot.game_time_seconds),
                game_time_limit_seconds=limit,
                seconds_remaining=remaining,
                race=self.config.race,
                enemy_race=enemy_race,
            ),
            economy=EconomyView(
                minerals=snapshot.minerals,
                vespene=snapshot.vespene,
                supply_used=snapshot.supply_used,
                supply_cap=snapshot.supply_cap,
                supply_left=max(0, int(snapshot.supply_cap) - int(snapshot.supply_used)),
                worker_count=worker_count,
                mining_worker_capacity=snapshot.info.get("ideal_worker_count"),
                army_supply=army_supply,
                mineral_income_per_minute=snapshot.info.get("mineral_income_per_minute"),
                vespene_income_per_minute=snapshot.info.get("vespene_income_per_minute"),
            ),
            map_control=MapControlView(
                own_base_count=map_own_base_count,
                known_enemy_base_count=known_enemy,
                unconfirmed_expansion_count=unconfirmed,
                base_resources=list(snapshot.info.get("base_resources") or []),
            ),
            zone_state=zone_state,
            map_topology=topology,
            production_priority=self.task_manager.production_priority_summary(),
            production=snapshot.info.get("production"),
            own_forces=self._own_forces_view(snapshot),
            abilities=race_view.abilities,
            units=dict(snapshot.units),
            buildings=dict(snapshot.buildings),
            building=building,
            training=training,
            research=research,
            combat=combat,
            scouting=race_view.scouting,
            upgrades=upgrades,
            structures=list(snapshot.info.get("structures") or []),
            base_count=own_base_count,
            zones=zones,
            **race_view.legacy_attributes,
            recent_events=list(self.task_manager.recent_events[-8:]),
            available_targets=targets,
            relevant_zone_ids=relevant,
            terminated=snapshot.terminated,
        )

    def _assigned_army_counts(self, snapshot) -> dict[str, int]:
        bound: dict[str, int] = {}
        progress = dict(snapshot.info.get("combat_progress") or {})
        for demand in self.task_manager.active_demands():
            if demand.action != "combat":
                continue
            row = progress.get(demand.demand_id) or {}
            alive = row.get("alive")
            units = alive if isinstance(alive, dict) else (demand.units or {})
            for name, count in units.items():
                bound[str(name)] = bound.get(str(name), 0) + int(count)
        for name, count in dict(snapshot.info.get("unavailable_army") or {}).items():
            bound[str(name)] = max(bound.get(str(name), 0), int(count))
        return bound

    def _own_forces_view(self, snapshot):
        return split_own_forces(
            snapshot.units,
            assigned=self._assigned_army_counts(snapshot),
            bunker_garrison=snapshot.info.get("bunker_garrison"),
        )

    def _idle_army_counts(self, snapshot) -> dict[str, int]:
        army = dict(split_own_forces(snapshot.units).army)
        bound = self._assigned_army_counts(snapshot)
        return {
            name: max(0, int(army.get(name, 0)) - int(bound.get(name, 0)))
            for name in set(army) | set(bound)
        }

    def _combat_summary(self, snapshot) -> dict[str, dict]:
        progress = dict(snapshot.info.get("combat_progress") or {})
        summary: dict[str, dict] = {}
        for demand in self.task_manager.active_demands():
            if demand.action != "combat":
                continue
            label = demand.group or "unassigned_group"
            row = progress.get(demand.demand_id) or {}
            alive = row.get("alive")
            if not isinstance(alive, dict):
                alive = dict(demand.units or {})
            summary[label] = {
                "status": "active",
                "style": demand.style,
                "target": demand.target,
                "requested": dict(demand.units or {}),
                "alive": {str(k): int(v) for k, v in alive.items()},
                "assigned": bool(row.get("assigned", True)),
                "nearest_zone": row.get("nearest_zone"),
            }
            if row.get("phase"):
                summary[label]["phase"] = "executing" if row["phase"] == "fight" else str(row["phase"])
            summary[label]["visible_enemy_nearby"] = row.get("visible_enemy_nearby")
            summary[label]["weapon_cooldown_active_count"] = row.get("weapon_cooldown_active_count")
            if "cloaked" in row:
                summary[label]["cloaked"] = dict(row["cloaked"])
            if row.get("forms"):
                summary[label]["forms"] = dict(row["forms"])
            if row.get("skill_evidence"):
                summary[label]["skill_evidence"] = dict(row["skill_evidence"])
            if "transport" in row:
                summary[label]["transport"] = dict(row["transport"])
            if row.get("end_reason"):
                summary[label]["end_reason"] = str(row["end_reason"])
        main_zone = next((z.get("zone_id") for z in snapshot.info.get("zone_state", [])
                          if z.get("zone_role") == "own_main"), None)
        group0_zone = snapshot.info.get("group0_zone_id") or main_zone
        summary["group_0"] = {
            "status": "active", "style": "defend", "target": group0_zone,
            "alive": {k: v for k, v in self._idle_army_counts(snapshot).items() if v > 0},
            "assigned": False, "phase": "engaging" if snapshot.info.get("group0_engaged") else "guarding",
        }
        return summary
