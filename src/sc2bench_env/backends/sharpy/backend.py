"""SharpyBackend: threaded SC2/Sharpy bridge for Environment.reset/step/close."""
from __future__ import annotations


import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from sc2bench_env.backends.base import Backend, BackendSnapshot
from sc2bench_env.backends.sharpy.structures import StructureRegistry
from sc2bench_env.backends.sharpy.zones import ZoneRegistry
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.races import require_supported_own_race
from sc2bench_env.interface.opponents import (
    AI_BUILD_ENUM_NAMES, DIFFICULTY_ENUM_NAMES, normalize_opponent, require_enemy_style,
)
from sc2bench_env.runtime.scheduler import DecisionTrigger, trigger_satisfied
from sc2bench_env.runtime.task import Demand, DemandState, train_birth_allowance
from sc2bench_env.runtime.task_manager import DemandUpdate

logger = logging.getLogger("sc2bench_env.backends.sharpy")


@dataclass
class _ControlRequest:
    """One quick_save or quick_load posted to the bot thread."""

    request_id: str
    operation: str
    logical_time_seconds: float
    done: threading.Event = field(default_factory=threading.Event)
    error: Optional[BaseException] = None
    bookmark_id: Optional[str] = None
    detail_hash: Optional[str] = None
    fingerprint: Any = None

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


def _ensure_runtime_paths() -> None:
    """Compatibility entry point: no sys.path or sibling-project fallbacks."""
    from sc2bench_env.backends.sharpy.runtime_config import register_sharpy_config
    register_sharpy_config()


@dataclass
class _Bridge:
    lock: threading.RLock = field(default_factory=threading.RLock)
    ready: threading.Event = field(default_factory=threading.Event)
    decision_reached: threading.Event = field(default_factory=threading.Event)
    stopped: threading.Event = field(default_factory=threading.Event)
    blocking_decisions: bool = True
    realtime: bool = False
    advance_allowed: threading.Event = field(default_factory=threading.Event)
    leave_requested: bool = False
    target_time: Optional[float] = None
    max_game_time: Optional[float] = None
    macro_specs: List[Dict[str, Any]] = field(default_factory=list)
    snapshot: BackendSnapshot = field(default_factory=BackendSnapshot)
    knowledge_snapshot: Optional[Dict[str, Any]] = None
    # Peak ready counts observed for incremental completion accounting.
    peak_ready: Dict[str, int] = field(default_factory=dict)
    last_reported_completed: Dict[str, int] = field(default_factory=dict)
    baselines: Dict[str, int] = field(default_factory=dict)
    task_meta: Dict[str, Tuple[str, str, int, int]] = field(default_factory=dict)
    in_production_units: Dict[str, int] = field(default_factory=dict)
    in_progress_buildings: Dict[str, int] = field(default_factory=dict)
    in_progress_research: Dict[str, int] = field(default_factory=dict)
    macro_errors: Dict[str, str] = field(default_factory=dict)
    waiting_reasons: Dict[str, str] = field(default_factory=dict)
    act_completed: Dict[str, int] = field(default_factory=dict)
    active_task_ids: List[str] = field(default_factory=list)
    active_trigger: Optional[DecisionTrigger] = None
    wait_started_at: float = 0.0
    zone_registry: ZoneRegistry = field(default_factory=ZoneRegistry)
    structure_registry: StructureRegistry = field(default_factory=StructureRegistry)
    replay_path: Optional[Path] = None
    seen_entity_tags: set = field(default_factory=set)
    entity_tracking_started: bool = False
    # Versus games share one notify event and wake the other bot on game end.
    companions: List[Any] = field(default_factory=list)
    notify: Optional[threading.Event] = None
    # Versus clients publish one outcome after every side has a raw result.
    raw_result_ready: bool = False
    raw_own_result: Any = None
    raw_all_results: Dict[Any, Any] = field(default_factory=dict)
    outcome_lock: Optional[threading.Lock] = None
    outcome_published: bool = False
    # One in-memory engine slot. A newer save replaces it.
    control_request: Optional[_ControlRequest] = None
    engine_bookmark: Optional[str] = None
    bookmark_generation: int = 0
    saved_logical_time: float = 0.0
    time_offset_seconds: float = 0.0
    discard_macro_tasks: bool = False
    engine_fingerprint: Any = None
    last_fingerprint: Any = None
    last_detail_hash: Optional[str] = None

    def get_macro_specs(self) -> List[Dict[str, Any]]:
        with self.lock:
            return list(self.macro_specs)

    def should_leave(self) -> bool:
        with self.lock:
            return self.leave_requested

    def request_leave(self, *, end_reason: Optional[str] = None) -> None:
        with self.lock:
            if end_reason is not None and not self.snapshot.terminated:
                self.snapshot.terminated = True
                self.snapshot.end_reason = end_reason
            self.leave_requested = True
            # Closing must wake a game waiting for its next agent decision.
            self.advance_allowed.set()
            self._signal_companions()

    def _signal_companions(self) -> None:
        """Wake the other bot without taking its lock."""
        for other in list(self.companions):
            other.advance_allowed.set()
            other.decision_reached.set()
        if self.notify is not None:
            self.notify.set()

    def wait_for_decision(self) -> None:
        """Wait outside the bridge lock; the caller can submit/close meanwhile."""
        if self.blocking_decisions:
            self.advance_allowed.wait()

    def post_control(
        self, operation: str, *, logical_time_seconds: float, timeout: float = 30.0,
    ) -> str:
        """Wake the paused bot, run one engine bookmark, then pause again."""
        from sc2bench_env.runtime.savepoint import SavepointError

        request = _ControlRequest(
            request_id=uuid4().hex,
            operation=operation,
            logical_time_seconds=float(logical_time_seconds),
        )
        with self.lock:
            if self.stopped.is_set() or not self.ready.is_set():
                raise SavepointError("The game is not paused for a bookmark")
            # A platform time limit keeps the client in the match when a bookmark
            # exists, so quick_load can still return to that moment. A real
            # result has already left the game and cannot be loaded.
            reload_after_clock = (
                operation == "quick_load"
                and self.snapshot.terminated
                and self.snapshot.end_reason == "time_limit"
                and self.engine_bookmark
                and not self.leave_requested
            )
            if self.snapshot.terminated and not reload_after_clock:
                raise SavepointError("The episode has already ended")
            if not self.blocking_decisions or self.realtime:
                raise SavepointError("Savepoints require a paused blocking game")
            if self.advance_allowed.is_set():
                raise SavepointError("The game is still advancing")
            if self.control_request is not None:
                raise SavepointError("A bookmark request is already running")
            self.control_request = request
            self.advance_allowed.set()
        if not request.done.wait(timeout):
            with self.lock:
                if self.control_request is request:
                    self.control_request = None
                    if not self.leave_requested:
                        self.advance_allowed.clear()
            raise SavepointError("The game did not finish the bookmark in time")
        if request.error is not None:
            raise SavepointError(str(request.error)) from request.error
        return str(request.bookmark_id or "")

    def take_control_request(self) -> Optional[_ControlRequest]:
        """Return the posted bookmark request without treating it as a game step."""
        with self.lock:
            return self.control_request

    def finish_control(self, request: _ControlRequest) -> None:
        """Record a successful bookmark and pause again unless the game is closing."""
        with self.lock:
            if request.operation == "quick_save":
                self.bookmark_generation += 1
                self.engine_bookmark = str(self.bookmark_generation)
                self.saved_logical_time = float(request.logical_time_seconds)
                request.bookmark_id = self.engine_bookmark
                self.engine_fingerprint = request.fingerprint
            if request.fingerprint is not None:
                self.last_fingerprint = request.fingerprint
            if request.detail_hash is not None:
                self.last_detail_hash = request.detail_hash
            if self.control_request is request:
                self.control_request = None
            if not self.leave_requested and not self.stopped.is_set():
                self.advance_allowed.clear()
        request.done.set()

    def fail_control(self, request: _ControlRequest, error: BaseException) -> None:
        request.error = error
        with self.lock:
            if self.control_request is request:
                self.control_request = None
            if not self.leave_requested and not self.stopped.is_set():
                self.advance_allowed.clear()
        request.done.set()

    def consume_discard_macro_tasks(self) -> bool:
        with self.lock:
            discard = self.discard_macro_tasks
            self.discard_macro_tasks = False
            return discard

    def on_frame(
        self,
        *,
        snapshot: BackendSnapshot,
        in_production_units: Dict[str, int],
        in_progress_buildings: Dict[str, int],
        in_progress_research: Dict[str, int],
        macro_errors: Dict[str, str],
        waiting_reasons: Optional[Dict[str, str]] = None,
        act_completed: Optional[Dict[str, int]] = None,
        scout_progress: Optional[Dict[str, Any]] = None,
        combat_progress: Optional[Dict[str, Any]] = None,
    ) -> bool:
        with self.lock:
            # BenchBot.time already includes the quick_load offset.
            snapshot.game_time_seconds = float(snapshot.game_time_seconds)
            snapshot.terminated = self.snapshot.terminated
            snapshot.result = self.snapshot.result
            snapshot.end_reason = self.snapshot.end_reason
            snapshot.info = dict(snapshot.info)
            if scout_progress is not None:
                snapshot.info["scout_progress"] = dict(scout_progress)
            else:
                snapshot.info.pop("scout_progress", None)
            if combat_progress is not None:
                snapshot.info["combat_progress"] = dict(combat_progress)
            else:
                snapshot.info.pop("combat_progress", None)
            self.snapshot = snapshot
            self.in_production_units = dict(in_production_units)
            self.in_progress_buildings = dict(in_progress_buildings)
            self.in_progress_research = dict(in_progress_research)
            self.macro_errors = dict(macro_errors)
            self.waiting_reasons = dict(waiting_reasons or {})
            if act_completed is not None:
                self.act_completed = dict(act_completed)

            under = dict(snapshot.info.get("under_construction") or {})
            upgrades = set(snapshot.info.get("upgrades") or [])
            births: Dict[Tuple[str, str], int] = {}
            tracked = ("ready_unit_tags" in snapshot.info and "building_entity_tags" in snapshot.info)
            if tracked:
                for action, key in [("train", "ready_unit_tags"), ("build", "building_entity_tags")]:
                    for name, tags in snapshot.info[key].items():
                        for tag in set(tags):
                            identity = (action, name, int(tag))
                            if identity in self.seen_entity_tags:
                                continue
                            self.seen_entity_tags.add(identity)
                            if self.entity_tracking_started:
                                births[(action, name)] = births.get((action, name), 0) + 1
                self.entity_tracking_started = True

            for task_id in self.active_task_ids:
                meta = self.task_meta.get(task_id)
                if meta is None:
                    continue
                action, target, _count = meta[0], meta[1], meta[2]
                batch = int(meta[3]) if len(meta) > 3 else 1
                if tracked and action in {"train", "build"}:
                    # Credit first-seen outputs once, oldest demand first. Initial
                    # entities, cargo and death cannot duplicate progress. A real
                    # type-changing morph is a new target identity and counts once.
                    # A batch such as two Zerglings from one larva can exceed the
                    # requested minimum; those extra births stay on this demand.
                    identity = (action, target)
                    previous = self.peak_ready.get(task_id, 0)
                    allowance = train_birth_allowance(_count, batch) if action == "train" else _count
                    credit_cap = max(0, allowance - previous)
                    credited = min(credit_cap, births.get(identity, 0))
                    births[identity] = births.get(identity, 0) - credited
                    self.peak_ready[task_id] = previous + credited
                    continue
                if action == "scan":
                    ready = int(self.act_completed.get(task_id, 0))
                elif action in {"call_mule", "supply_drop", "chrono_boost", "inject_larva", "spawn_creep_tumor", "morph_townhall", "scout", "combat"}:
                    ready = int(self.act_completed.get(task_id, 0))
                elif action == "build":
                    # Build action completes when the unfinished entity appears.
                    ready = int(snapshot.buildings.get(target, 0)) + int(under.get(target, 0))
                elif action == "research":
                    ready = 1 if target in self.in_progress_research or target in upgrades else 0
                else:
                    ready = snapshot.owned_count(action, target)
                baseline = self.baselines.get(task_id, 0)
                completed = max(0, ready - baseline)
                prev_peak = self.peak_ready.get(task_id, 0)
                self.peak_ready[task_id] = max(prev_peak, completed)

            # Enforce the platform clock on EVERY frame, including asynchronous
            # agent inference. BurnySC2's legacy run_game limit uses another clock.
            hold_for_bookmark = False
            if (not self.snapshot.terminated and self.max_game_time is not None
                    and snapshot.game_time_seconds >= self.max_game_time):
                self.snapshot.terminated = True
                self.snapshot.result = "Result.Tie"
                self.snapshot.end_reason = "time_limit"
                if self.engine_bookmark and not self.leave_requested:
                    hold_for_bookmark = True
                else:
                    self.request_leave()

            if not self.ready.is_set():
                self.ready.set()

            if hold_for_bookmark:
                # Stay paused inside the match. quick_load is still possible.
                self.advance_allowed.clear()
                self.decision_reached.set()
                if self.notify is not None:
                    self.notify.set()
            elif self.snapshot.terminated:
                self.advance_allowed.set()
                self.decision_reached.set()
                if self.notify is not None:
                    self.notify.set()
            elif self.active_trigger is not None and trigger_satisfied(
                self.active_trigger,
                snapshot=self.snapshot,
                wait_started_at=self.wait_started_at,
            ):
                self.active_trigger = None
                if self.blocking_decisions:
                    # Freeze at the published snapshot BEFORE waking step().
                    self.advance_allowed.clear()
                self.decision_reached.set()
                if self.notify is not None:
                    self.notify.set()
            return self.blocking_decisions and not self.advance_allowed.is_set()

    def note_raw_result(self, own_result: Any, all_results: Any) -> None:
        """Remember one client's protocol result and publish when the group is complete."""
        with self.lock:
            self.raw_result_ready = True
            self.raw_own_result = own_result
            self.raw_all_results = dict(all_results or {})
        self.publish_group_outcome()

    def publish_group_outcome(self) -> None:
        """Write one reconciled result onto every versus client, once."""
        from sc2bench_env.backends.sharpy.bot import (
            has_townhall, reconcile_versus_group, reconcile_versus_result,
        )

        group = [self, *list(self.companions)]
        lock = self.outcome_lock
        if lock is None:
            lock = threading.Lock()
            for bridge in group:
                if bridge.outcome_lock is None:
                    bridge.outcome_lock = lock
        with lock:
            if any(not bridge.raw_result_ready for bridge in group):
                return
            if any(bridge.outcome_published for bridge in group):
                return
            rows = []
            for bridge in group:
                result, reason = reconcile_versus_result(
                    bridge.raw_own_result, bridge.raw_all_results,
                )
                rows.append((result, reason, has_townhall(bridge.snapshot)))
            outcomes = reconcile_versus_group(rows)
            for bridge, (result, reason) in zip(group, outcomes):
                bridge.outcome_published = True
                bridge.on_game_end(result, end_reason=reason)

    def on_game_end(self, result: str, end_reason: Optional[str] = None) -> None:
        with self.lock:
            # client.leave() may produce Defeat/Tie after an artificial cutoff.
            # Preserve the actual cause; genuine runtime errors still take priority.
            if not self.snapshot.terminated or result.startswith("error:"):
                self.snapshot.result = result
                if result.startswith("error:"):
                    self.snapshot.end_reason = "backend_error"
                else:
                    self.snapshot.end_reason = end_reason or "game_ended"
            self.snapshot.terminated = True
            self.decision_reached.set()
            self.ready.set()
            self.stopped.set()
            self.advance_allowed.set()
            self._signal_companions()


class SharpyBackend(Backend):
    """Launch SC2 in a worker thread and expose Backend.run_until semantics."""

    def __init__(self, *, startup_timeout_seconds: float = 120.0) -> None:
        self.startup_timeout_seconds = startup_timeout_seconds
        self._bridge = _Bridge()
        self._thread: Optional[threading.Thread] = None
        self._config: Optional[EpisodeConfig] = None
        self._adapter = None
        self._game_error: Optional[BaseException] = None
        self._replay_path: Optional[Path] = None
        self._terminal_settled = threading.Event()
        self.knowledge_snapshot: Optional[Dict[str, Any]] = None
        self._versus = False

    def set_replay_path(self, path: Optional[Path]) -> None:
        self._replay_path = path.resolve() if path is not None else None

    def start_episode(self, config: EpisodeConfig) -> BackendSnapshot:
        require_supported_own_race(config.race)
        self.close_episode()
        self._versus = False
        self._config = config
        self._bridge = _Bridge()
        self._bridge.blocking_decisions = config.blocking_decisions
        self._bridge.realtime = config.realtime
        if not config.blocking_decisions:
            self._bridge.advance_allowed.set()
        self._bridge.max_game_time = config.game_time_limit_seconds
        self._bridge.replay_path = self._replay_path
        self._bridge.zone_registry.reset()
        self._bridge.structure_registry.reset()
        self._game_error = None
        self._terminal_settled = threading.Event()

        _ensure_runtime_paths()
        from sc2bench_env.backends.sharpy.races import get_adapter

        self._adapter = get_adapter(config.race)

        self._thread = threading.Thread(
            target=self._run_game_thread,
            name="sc2bench-sharpy",
            daemon=True,
        )
        self._thread.start()

        if not self._bridge.ready.wait(timeout=self.startup_timeout_seconds):
            self.close_episode()
            raise TimeoutError(
                f"SC2/Sharpy did not become ready within {self.startup_timeout_seconds}s"
            )
        if self._game_error is not None:
            raise RuntimeError(f"SC2/Sharpy failed to start: {self._game_error}") from self._game_error
        self.knowledge_snapshot = getattr(self._bridge, "knowledge_snapshot", None)
        if self.knowledge_snapshot is not None:
            from sc2bench_env.catalog.knowledge import activate_game_data

            activate_game_data(self.knowledge_snapshot)
        return self.snapshot()

    def submit(self, tasks: List[Demand]) -> None:
        if self._adapter is None:
            raise RuntimeError("SharpyBackend is not started")
        specs: List[Dict[str, Any]] = []
        baselines: Dict[str, int] = {}
        task_meta: Dict[str, Tuple[str, str, int, int]] = {}
        active_ids: List[str] = []

        # Each Act's absolute target includes only preceding same-type work, not
        # later orders. This preserves interleaved production priorities.
        ordered_tasks = sorted(
            (task for task in tasks if task.is_active),
            key=lambda task: (int(task.order_index), str(task.demand_id)),
        )
        snap = self.snapshot()
        train_prefix: Dict[str, int] = {}
        build_prefix: Dict[str, int] = {}

        for task in ordered_tasks:
            active_ids.append(task.demand_id)
            baseline = int(task.baseline_owned)
            baselines[task.demand_id] = baseline
            task_meta[task.demand_id] = (
                task.action, task.target, task.count, max(1, int(task.production_batch_size)),
            )
            if task.action == "train":
                key = str(task.target or "")
                train_prefix[key] = train_prefix.get(key, 0) + max(0, int(task.remaining))
                to_count = self._adapter.production_owned_count(snap, "train", key) + train_prefix[key]
            elif task.action == "build":
                key = str(task.target or "")
                build_prefix[key] = build_prefix.get(key, 0) + max(0, int(task.remaining))
                owned = self._adapter.production_owned_count(snap, "build", key)
                to_count = owned + build_prefix[key]
            else:
                to_count = baseline + max(1, int(task.count))
            task_ref = task

            def _factory(t: Demand = task_ref, absolute: int = to_count) -> Any:
                return self._adapter.create_act(t, to_count=absolute)

            specs.append(
                {
                    "task_id": task.demand_id,
                    "action": task.action,
                    "target": task.target,
                    "to": task.to,
                    "style": task.style,
                    "group": task.group,
                    "withdrawing": task.withdrawing,
                    "command_revision": task.command_revision,
                    "membership_revision": task.membership_revision,
                    "merge_source_ids": list(task.merge_source_ids),
                    "transfer_requests": [dict(item) for item in task.transfer_requests],
                    "merge_pending_into": task.merge_pending_into,
                    "retreat_method": task.retreat_method,
                    "recall_status": task.recall_status,
                    "units": dict(task.units or {}),
                    "to_count": to_count,
                    "order_index": int(task.order_index),
                    "factory": _factory,
                }
            )

        with self._bridge.lock:
            previous_ids = set(self._bridge.active_task_ids)
            self._bridge.macro_specs = specs
            self._bridge.baselines = baselines
            self._bridge.task_meta = task_meta
            self._bridge.active_task_ids = active_ids
            for task_id in active_ids:
                if task_id not in previous_ids:
                    self._bridge.peak_ready[task_id] = 0
                    self._bridge.last_reported_completed[task_id] = 0
            for task_id in list(self._bridge.peak_ready):
                if task_id not in active_ids:
                    self._bridge.peak_ready.pop(task_id, None)
                    self._bridge.last_reported_completed.pop(task_id, None)

    def prepare_for_versus(self, config: EpisodeConfig) -> None:
        """Set up this side of a shared game without launching SC2."""
        require_supported_own_race(config.race)
        if not config.blocking_decisions:
            raise ValueError("versus matches require blocking decisions")
        self.close_episode()
        self._versus = True
        self._config = config
        self._bridge = _Bridge()
        self._bridge.blocking_decisions = True
        self._bridge.realtime = config.realtime
        self._bridge.max_game_time = config.game_time_limit_seconds
        self._bridge.replay_path = self._replay_path
        self._bridge.zone_registry.reset()
        self._bridge.structure_registry.reset()
        self._game_error = None
        self._terminal_settled = threading.Event()
        _ensure_runtime_paths()
        from sc2bench_env.backends.sharpy.races import get_adapter
        self._adapter = get_adapter(config.race)

    def is_waiting(self) -> bool:
        with self._bridge.lock:
            return (self._bridge.ready.is_set() and not self._bridge.advance_allowed.is_set()
                    and not self._bridge.snapshot.terminated)

    def release(self, trigger: DecisionTrigger) -> None:
        """Arm this side's next pause and let its bot continue. Do not wait."""
        with self._bridge.lock:
            if self._bridge.snapshot.terminated or self._bridge.stopped.is_set():
                self._bridge.advance_allowed.set()
                if self._bridge.notify is not None:
                    self._bridge.notify.set()
                return
            now = self._bridge.snapshot.game_time_seconds
            self._bridge.active_trigger = trigger
            self._bridge.wait_started_at = now
            self._bridge.target_time = now + float(trigger.interval_seconds)
            if trigger.max_game_time_seconds is not None:
                self._bridge.max_game_time = trigger.max_game_time_seconds
            self._bridge.decision_reached.clear()
            self._bridge.advance_allowed.set()

    def run_until(self, trigger: DecisionTrigger) -> bool:
        with self._bridge.lock:
            if self._bridge.snapshot.terminated or self._bridge.stopped.is_set():
                return True
            now = self._bridge.snapshot.game_time_seconds
            self._bridge.active_trigger = trigger
            self._bridge.wait_started_at = now
            self._bridge.target_time = now + float(trigger.interval_seconds)
            if trigger.max_game_time_seconds is not None:
                self._bridge.max_game_time = trigger.max_game_time_seconds
            self._bridge.decision_reached.clear()
            # submit() has already installed the entire decision's task list.
            self._bridge.advance_allowed.set()

        while not self._bridge.decision_reached.wait(timeout=1.0):
            if self._bridge.stopped.is_set():
                break
            if self._thread is not None and not self._thread.is_alive():
                break
            if self._game_error is not None:
                break

        with self._bridge.lock:
            return bool(self._bridge.snapshot.terminated)

    def collect_updates(self) -> List[DemandUpdate]:
        updates: List[DemandUpdate] = []
        with self._bridge.lock:
            under = dict(self._bridge.snapshot.info.get("under_construction") or {})
            en_route = dict(self._bridge.snapshot.info.get("workers_en_route") or {})
            # Remaining en-route slots per target, consumed by earliest demands.
            en_route_left = dict(en_route)
            in_prod_left = dict(self._bridge.in_production_units)

            # submit() already publishes order_index order. Living baselines
            # can decrease after casualties and must never reorder demands.
            ordered_ids = list(self._bridge.active_task_ids)

            for task_id in ordered_ids:
                meta = self._bridge.task_meta.get(task_id)
                if meta is None:
                    continue
                action, target, count = meta[0], meta[1], meta[2]
                peak = int(self._bridge.peak_ready.get(task_id, 0))
                last = int(self._bridge.last_reported_completed.get(task_id, 0))
                delta = max(0, peak - last)
                self._bridge.last_reported_completed[task_id] = peak

                failure = self._bridge.macro_errors.get(task_id)
                state: Optional[DemandState] = None
                waiting_for: Optional[str] = self._bridge.waiting_reasons.get(task_id)
                assigned_in_prod: Optional[int] = None
                transfer_ids = []
                if failure:
                    state = DemandState.FAILED
                elif action == "build":
                    if peak >= count:
                        state = DemandState.COMPLETED
                    elif en_route_left.get(target, 0) > 0:
                        state = DemandState.WORKER_EN_ROUTE
                        en_route_left[target] = en_route_left.get(target, 0) - 1
                    elif under.get(target, 0) > 0 and delta == 0 and peak < count:
                        # Entity exists for another demand; this one still waiting.
                        state = DemandState.WAITING_TO_START
                        # Another task's entity does not prove a money shortage.
                        # Keep the execution-side reason, or unknown (None).
                    else:
                        state = DemandState.WAITING_TO_START
                elif action == "train":
                    batch = int(meta[3]) if len(meta) > 3 else 1
                    birth_goal = train_birth_allowance(count, batch)
                    in_prod = min(max(0, birth_goal - peak), int(in_prod_left.get(target, 0)))
                    in_prod_left[target] = max(0, int(in_prod_left.get(target, 0)) - in_prod)
                    assigned_in_prod = in_prod
                    if peak >= birth_goal:
                        state = DemandState.COMPLETED
                    elif in_prod > 0:
                        state = DemandState.IN_PRODUCTION
                    else:
                        state = DemandState.WAITING_TO_START
                elif action == "research":
                    if peak >= 1:
                        state = DemandState.COMPLETED
                    elif int(self._bridge.in_progress_research.get(target, 0)) > 0:
                        state = DemandState.IN_PROGRESS
                    else:
                        state = DemandState.WAITING_TO_START
                elif action in {"scan", "call_mule", "supply_drop", "chrono_boost", "inject_larva", "spawn_creep_tumor"}:
                    if peak >= 1:
                        state = DemandState.COMPLETED
                        waiting_for = None
                    else:
                        if self._adapter is None:
                            raise RuntimeError("SharpyBackend is not started")
                        state, waiting_for = self._adapter.ability_task_state(
                            action, self._bridge.snapshot, waiting_for)
                elif action == "morph_townhall":
                    if peak >= 1:
                        state = DemandState.COMPLETED
                    else:
                        state = DemandState.WAITING_TO_START
                elif action == "scout":
                    if peak >= 1:
                        state = DemandState.COMPLETED
                    else:
                        state = DemandState.IN_PROGRESS
                elif action == "combat":
                    progress = dict(self._bridge.snapshot.info.get("combat_progress") or {})
                    row = dict(progress.get(task_id) or {})
                    end_reason = row.get("end_reason")
                    if end_reason:
                        state = DemandState.COMPLETED
                        updates.append(
                            DemandUpdate(
                                demand_id=task_id,
                                action=action,
                                target=target,
                                produced_delta=0,
                                state=state,
                                end_reason=str(end_reason),
                                details={
                                    "transferred_units": dict(
                                        row.get("merge_transferred_units") or {}
                                    )
                                },
                            )
                        )
                        self._bridge.last_reported_completed[task_id] = peak
                        continue
                    failure = self._bridge.macro_errors.get(task_id)
                    if failure and str(failure).startswith("insufficient_units"):
                        state = DemandState.FAILED
                    else:
                        state = DemandState.IN_PROGRESS
                    transfer_ids = row.get("applied_transfer_ids") or []
                else:
                    state = DemandState.WAITING_TO_START

                updates.append(
                    DemandUpdate(
                        demand_id=task_id,
                        action=action,
                        target=target,
                        produced_delta=delta,
                        state=state,
                        waiting_for=waiting_for,
                        failure_reason=failure,
                        in_progress=assigned_in_prod,
                        details=(
                            {"applied_transfer_ids": list(transfer_ids)}
                            if action == "combat" and transfer_ids else {}
                        ),
                    )
                )
        return updates

    def snapshot(self) -> BackendSnapshot:
        # BenchBot.on_end publishes the provisional SC2 result before
        # run_game finishes replay/transport cleanup. If that cleanup reveals
        # a protocol or connection failure, _run_game_thread replaces the
        # provisional Defeat/Tie with backend_error. Do not let the Environment
        # finalize the episode in the small window between those two events.
        with self._bridge.lock:
            terminal = bool(self._bridge.snapshot.terminated)
        if (
            terminal
            and self._thread is not None
            and self._thread.is_alive()
            and threading.current_thread() is not self._thread
        ):
            self._terminal_settled.wait(timeout=30.0)
        with self._bridge.lock:
            snap = self._bridge.snapshot
            return BackendSnapshot(
                game_time_seconds=snap.game_time_seconds,
                minerals=snap.minerals,
                vespene=snap.vespene,
                supply_used=snap.supply_used,
                supply_cap=snap.supply_cap,
                units=dict(snap.units),
                buildings=dict(snap.buildings),
                terminated=snap.terminated,
                result=snap.result,
                info=dict(snap.info),
                end_reason=snap.end_reason,
            )

    def supports_savepoints(self) -> bool:
        # One quick_save slot is the whole SC2 process. A versus match has two
        # clients, so bookmarking one side would roll the other back with it.
        if self._versus:
            return False
        config = self._config
        return bool(config is not None and config.blocking_decisions and not config.realtime)

    def debug_end_match(self) -> None:
        """Ask SC2 to end this paused match. Used to check a real result."""
        from sc2bench_env.runtime.savepoint import SavepointError

        if not self.supports_savepoints():
            raise SavepointError("Savepoints require a paused blocking game")
        self._bridge.post_control("debug_end", logical_time_seconds=0.0)

    def create_engine_bookmark(self) -> str:
        from sc2bench_env.runtime.savepoint import SavepointError

        if not self.supports_savepoints():
            raise SavepointError("Savepoints require a paused blocking game")
        logical = float(self.snapshot().game_time_seconds)
        return self._bridge.post_control("quick_save", logical_time_seconds=logical)

    def restore_engine_bookmark(self, bookmark: str) -> BackendSnapshot:
        from sc2bench_env.runtime.savepoint import SavepointError

        if not self.supports_savepoints():
            raise SavepointError("Savepoints require a paused blocking game")
        if bookmark != self._bridge.engine_bookmark:
            raise SavepointError("This bookmark is no longer the live engine slot")
        self._bridge.post_control(
            "quick_load", logical_time_seconds=self._bridge.saved_logical_time,
        )
        return self.snapshot()

    def publish_observation(self) -> BackendSnapshot:
        """Rebuild the platform snapshot after the savepoint registries are restored."""
        from sc2bench_env.runtime.savepoint import SavepointError

        if not self.supports_savepoints():
            raise SavepointError("Savepoints require a paused blocking game")
        self._bridge.post_control("publish", logical_time_seconds=0.0)
        return self.snapshot()

    def capture_engine_detail(self) -> str:
        """Hash the paused simulation, including every unit position and order."""
        from sc2bench_env.runtime.savepoint import SavepointError

        if not self.supports_savepoints():
            raise SavepointError("Savepoints require a paused blocking game")
        self._bridge.post_control("inspect", logical_time_seconds=0.0)
        if not self._bridge.last_detail_hash:
            raise SavepointError("The game did not return a simulation fingerprint")
        return str(self._bridge.last_detail_hash)

    def saved_engine_detail(self) -> str:
        """Return the simulation hash stored with the current engine bookmark."""
        from sc2bench_env.runtime.savepoint import SavepointError

        fingerprint = self._bridge.engine_fingerprint
        if fingerprint is None or not getattr(fingerprint, "digest", None):
            raise SavepointError("This game has no saved simulation fingerprint")
        return str(fingerprint.digest)

    def export_runtime_state(self) -> dict[str, Any]:
        """Copy accounting and registries. Macro factories are rebuilt by submit()."""
        from copy import deepcopy

        bridge = self._bridge
        with bridge.lock:
            return {
                "peak_ready": dict(bridge.peak_ready),
                "last_reported_completed": dict(bridge.last_reported_completed),
                "baselines": dict(bridge.baselines),
                "task_meta": {key: tuple(value) for key, value in bridge.task_meta.items()},
                "active_task_ids": list(bridge.active_task_ids),
                "seen_entity_tags": set(bridge.seen_entity_tags),
                "entity_tracking_started": bool(bridge.entity_tracking_started),
                "act_completed": dict(bridge.act_completed),
                "in_production_units": dict(bridge.in_production_units),
                "in_progress_buildings": dict(bridge.in_progress_buildings),
                "in_progress_research": dict(bridge.in_progress_research),
                "macro_errors": dict(bridge.macro_errors),
                "waiting_reasons": dict(bridge.waiting_reasons),
                "zone_registry": deepcopy(bridge.zone_registry),
                "structure_registry": deepcopy(bridge.structure_registry),
            }

    def import_runtime_state(self, state: dict[str, Any]) -> None:
        """Restore accounting after the engine bookmark. The next sync rebuilds Acts."""
        from copy import deepcopy

        bridge = self._bridge
        with bridge.lock:
            bridge.peak_ready = dict(state.get("peak_ready") or {})
            bridge.last_reported_completed = dict(state.get("last_reported_completed") or {})
            bridge.baselines = dict(state.get("baselines") or {})
            bridge.task_meta = {
                key: tuple(value) for key, value in dict(state.get("task_meta") or {}).items()
            }
            bridge.active_task_ids = list(state.get("active_task_ids") or [])
            bridge.seen_entity_tags = set(state.get("seen_entity_tags") or set())
            bridge.entity_tracking_started = bool(state.get("entity_tracking_started"))
            bridge.act_completed = dict(state.get("act_completed") or {})
            bridge.in_production_units = dict(state.get("in_production_units") or {})
            bridge.in_progress_buildings = dict(state.get("in_progress_buildings") or {})
            bridge.in_progress_research = dict(state.get("in_progress_research") or {})
            bridge.macro_errors = dict(state.get("macro_errors") or {})
            bridge.waiting_reasons = dict(state.get("waiting_reasons") or {})
            if "zone_registry" in state:
                bridge.zone_registry = deepcopy(state["zone_registry"])
            if "structure_registry" in state:
                bridge.structure_registry = deepcopy(state["structure_registry"])
            bridge.discard_macro_tasks = True

    def close_episode(self) -> None:
        self._bridge.request_leave(end_reason="closed_by_caller")
        self._bridge.decision_reached.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=30.0)
        self._thread = None
        with self._bridge.lock:
            self._bridge.macro_specs = []
            self._bridge.stopped.set()

    # --- worker -----------------------------------------------------------------

    def _run_game_thread(self) -> None:
        assert self._config is not None
        from sc2bench_env.backends.sharpy.compat import register_modern_terran_abilities
        register_modern_terran_abilities()
        assert self._adapter is not None
        try:
            _ensure_runtime_paths()

            import asyncio
            import signal

            from sc2 import maps, run_game
            from sc2.player import Bot

            from sc2bench_env.backends.sharpy.bot import BenchBot

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # burnysc2 registers SIGINT inside SC2Process; that only works on the
            # main thread. Environment keeps the agent loop on the caller thread.
            _orig_signal = signal.signal

            def _threadsafe_signal(signum, handler):  # type: ignore[no-untyped-def]
                try:
                    return _orig_signal(signum, handler)
                except ValueError:
                    return signal.SIG_DFL

            signal.signal = _threadsafe_signal  # type: ignore[assignment]

            bot = BenchBot(self._bridge, self._adapter)
            race = _parse_race(self._config.race)

            # Stay lockstep. config.realtime only paces the decision wait at 1x;
            # the advance window steps as fast as the machine allows.
            kwargs: Dict[str, Any] = {"realtime": False}
            # BenchBot saves during on_end, before SC2 starts closing the
            # websocket. Passing save_replay_as here would make BurnySC2 send a
            # second, later RequestSaveReplay over a closing transport.
            # _Bridge owns the time limit using ai.time, the same clock exposed
            # in Observation. Do not enable BurnySC2's legacy 0.725/16 cutoff.
            if self._config.seed is not None:
                kwargs["random_seed"] = int(self._config.seed)

            game_result = run_game(
                maps.get(self._config.map_name),
                [
                    Bot(race, bot),
                    _create_computer_opponent(self._config),
                ],
                **kwargs,
            )
            if not self._bridge.snapshot.terminated:
                if game_result is None:
                    raise RuntimeError("SC2 runner returned without a game result")
                self._bridge.on_game_end(str(game_result))
        except BaseException as exc:
            from sc2.protocol import ProtocolError

            replay = self._bridge.replay_path
            if (self._bridge.leave_requested and isinstance(exc, ProtocolError)
                    and "Not in a game" in str(exc) and replay is not None and replay.is_file()
                    and replay.stat().st_size > 0):
                # Explicit leave already saved the replay in BenchBot. Preserve
                # the recorded result if BurnySC2's later leave/quit observes
                # that SC2 has already closed the game.
                logger.warning("Post-leave cleanup observed a closed game: %s", exc)
            else:
                logger.exception("Sharpy game thread failed")
                self._game_error = exc
                self._bridge.on_game_end(f"error:{exc}")
        finally:
            self._terminal_settled.set()
            self._bridge.stopped.set()
            self._bridge.ready.set()
            self._bridge.decision_reached.set()
            self._bridge.advance_allowed.set()


def _parse_race(name: str) -> "Race":
    from sc2.data import Race

    mapping = {
        "terran": Race.Terran,
        "protoss": Race.Protoss,
        "zerg": Race.Zerg,
        "random": Race.Random,
    }
    key = (name or "terran").strip().lower()
    if key not in mapping:
        raise ValueError(f"unsupported race: {name!r}")
    return mapping[key]


def _create_computer_opponent(config: EpisodeConfig):
    from sc2.player import Computer
    return Computer(_parse_race(config.enemy_race), _parse_difficulty(config.opponent),
                    ai_build=_parse_ai_build(config.enemy_style))


def _parse_ai_build(style: str):
    from sc2.data import AIBuild
    return getattr(AIBuild, AI_BUILD_ENUM_NAMES[require_enemy_style(style)])


def _parse_difficulty(opponent: str):
    from sc2.data import Difficulty

    canonical = normalize_opponent(opponent)
    return getattr(Difficulty, DIFFICULTY_ENUM_NAMES[canonical])
