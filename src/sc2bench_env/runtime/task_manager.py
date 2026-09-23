"""Ordered demand queue: append, idempotent research, cancel, action_id retry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set

from sc2bench_env.interface.actions import (
    ActionValidationError,
    DecisionBatch,
    GameAction,
    parse_decision,
)
from sc2bench_env.interface.feedback import ActionReceipt
from sc2bench_env.interface.races import require_supported_own_race
from sc2bench_env.interface.scouting import route_payload
from sc2bench_env.runtime.task import (
    ACTIVE_DEMAND_STATES,
    Demand,
    DemandState,
)


@dataclass
class DemandUpdate:
    """Backend-reported progress for one demand_id or action/target bucket."""

    demand_id: Optional[str] = None
    action: Optional[str] = None
    target: Optional[str] = None
    produced_delta: int = 0
    state: Optional[DemandState] = None
    waiting_for: Optional[str] = None
    failure_reason: Optional[str] = None
    end_reason: Optional[str] = None
    # Legacy aliases used by older backend code paths.
    completed_delta: int = 0
    in_progress: Optional[int] = None
    status: Optional[DemandState] = None


# Back-compat name.
TaskUpdate = DemandUpdate


@dataclass
class TaskManager:
    """Pure-Python demand runtime used by Environment."""

    demands: Dict[str, Demand] = field(default_factory=dict)
    order_counter: int = 0
    seen_action_ids: Set[str] = field(default_factory=set)
    recent_events: List[dict] = field(default_factory=list)
    event_limit: int = 16
    group_counter: int = 0
    race: str = "terran"

    def __post_init__(self) -> None:
        require_supported_own_race(self.race)

    # Back-compat for older Environment code.
    @property
    def tasks(self) -> Dict[str, Demand]:
        return self.demands

    def active_demands(self) -> List[Demand]:
        return sorted(
            (d for d in self.demands.values() if d.is_active),
            key=lambda d: d.order_index,
        )

    def active_tasks(self) -> List[Demand]:
        return self.active_demands()

    def reset(self, *, race: Optional[str] = None) -> None:
        selected_race = self.race if race is None else race
        require_supported_own_race(selected_race)
        self.race = selected_race
        self.demands.clear()
        self.order_counter = 0
        self.seen_action_ids.clear()
        self.recent_events.clear()
        self.group_counter = 0

    def find_active_research(self, target: str) -> Optional[Demand]:
        for demand in self.demands.values():
            if (
                demand.is_active
                and demand.action == "research"
                and demand.target == target
            ):
                return demand
        return None

    def submit_decision(
        self,
        decision: DecisionBatch | Sequence[dict] | None,
        *,
        game_time: float,
        baseline_owned: Dict[tuple[str, str], int] | None = None,
        known_upgrades: Set[str] | None = None,
        researching: Set[str] | None = None,
        idle_army: Dict[str, int] | None = None,
        bunker_garrison: Dict[str, int] | None = None,
    ) -> List[ActionReceipt]:
        """Apply one validated decision batch in array order."""
        receipts: List[ActionReceipt] = []
        baselines = baseline_owned or {}
        upgrades = known_upgrades or set()
        in_research = researching or set()
        available_army = dict(idle_army or {})

        try:
            if isinstance(decision, DecisionBatch):
                batch = decision
            else:
                batch = parse_decision(decision, race=self.race)
        except ActionValidationError as exc:
            receipts.append(
                ActionReceipt(
                    action="?",
                    result="rejected",
                    reason=str(exc),
                )
            )
            return receipts

        for action in batch.actions:
            receipts.append(
                self._apply_game_action(
                    action,
                    game_time=game_time,
                    baselines=baselines,
                    upgrades=upgrades,
                    researching=in_research,
                    idle_army=available_army,
                    bunker_garrison=bunker_garrison,
                )
            )
        return receipts

    # Back-compat wrapper used by older Environment.step paths.
    def submit(
        self,
        raw_actions: Sequence[dict | GameAction] | DecisionBatch | None,
        *,
        game_time: float,
        baseline_owned: Dict[tuple[str, str], int] | None = None,
        known_upgrades: Set[str] | None = None,
        researching: Set[str] | None = None,
    ) -> List[ActionReceipt]:
        if isinstance(raw_actions, DecisionBatch):
            return self.submit_decision(
                raw_actions,
                game_time=game_time,
                baseline_owned=baseline_owned,
                known_upgrades=known_upgrades,
                researching=researching,
            )
        # Legacy callers without trailing advance: wrap with a short advance.
        items: List[dict] = []
        for item in raw_actions or []:
            if isinstance(item, GameAction):
                items.append(item.to_tool_call())
                continue
            payload = dict(item)
            if "action" in payload and "name" not in payload:
                name = payload.pop("action")
                payload = {"name": name, "arguments": payload}
            items.append(payload)
        if not items or items[-1].get("name") != "advance":
            items.append({"name": "advance", "arguments": {"seconds": 5}})
        return self.submit_decision(
            items,
            game_time=game_time,
            baseline_owned=baseline_owned,
            known_upgrades=known_upgrades,
            researching=researching,
        )

    def _apply_game_action(
        self,
        action: GameAction,
        *,
        game_time: float,
        baselines: Dict[tuple[str, str], int],
        upgrades: Set[str],
        researching: Set[str],
        idle_army: Dict[str, int],
        bunker_garrison: Dict[str, int] | None = None,
    ) -> ActionReceipt:
        if action.action_id and action.action_id in self.seen_action_ids:
            return ActionReceipt(
                action=action.action,
                target=action.target,
                count=action.count,
                result="ignored_duplicate_action_id",
                reason=f"action_id {action.action_id!r} already applied",
                group=action.group,
                action_id=action.action_id,
                style=action.style,
            )

        if action.action == "cancel":
            cleared = self._cancel_waiting(
                target_action=action.target_action or "",
                target=action.target or "",
                game_time=game_time,
            )
            if action.action_id:
                self.seen_action_ids.add(action.action_id)
            return ActionReceipt(
                action="cancel",
                target=action.target,
                target_action=action.target_action,
                result="accepted",
                reason=f"cleared_waiting={cleared}",
                action_id=action.action_id,
            )

        if action.action == "research":
            target = action.target or ""
            if target in upgrades:
                if action.action_id:
                    self.seen_action_ids.add(action.action_id)
                return ActionReceipt(
                    action="research",
                    target=target,
                    result="idempotent_noop",
                    reason="already_researched",
                    action_id=action.action_id,
                )
            if target in researching:
                if action.action_id:
                    self.seen_action_ids.add(action.action_id)
                return ActionReceipt(
                    action="research",
                    target=target,
                    result="idempotent_noop",
                    reason="research_already_queued",
                    action_id=action.action_id,
                )
            existing = self.find_active_research(target)
            if existing is not None:
                if action.action_id:
                    self.seen_action_ids.add(action.action_id)
                return ActionReceipt(
                    action="research",
                    target=target,
                    result="idempotent_noop",
                    reason="research_already_active",
                    action_id=action.action_id,
                )
            # Action already succeeded earlier this episode (queued), even if the
            # upgrade has not finished yet.
            prior = [
                d
                for d in self.demands.values()
                if d.action == "research"
                and d.target == target
                and d.state == DemandState.COMPLETED
            ]
            if prior:
                if action.action_id:
                    self.seen_action_ids.add(action.action_id)
                return ActionReceipt(
                    action="research",
                    target=target,
                    result="idempotent_noop",
                    reason="research_already_accepted",
                    action_id=action.action_id,
                )

        if action.action == "scout":
            # New scout route replaces unfinished prior scout demand.
            for demand in list(self.demands.values()):
                if demand.action == "scout" and demand.is_active:
                    demand.mark_cancelled(game_time)
                    self._push_event(
                        {
                            "type": "demand_cancelled",
                            "action": "scout",
                            "target": demand.target,
                            "reason": "replaced",
                        }
                    )

        if action.action in {"combat", "retreat"} and action.group:
            existing = next((d for d in self.active_demands()
                             if d.action == "combat" and d.group == action.group), None)
            if existing is None:
                return ActionReceipt(action=action.action, result="rejected", group=action.group,
                                     reason="group_not_found")
            withdrawing = action.action == "retreat"
            unchanged = (existing.withdrawing if withdrawing else
                         not existing.withdrawing and existing.style == action.style and existing.target == action.target)
            if not unchanged:
                existing.withdrawing = withdrawing
                if not withdrawing:
                    existing.style, existing.target = action.style, action.target or ""
                existing.command_revision += 1
                existing.updated_at = game_time
                self._push_event({"type": "group_order_updated", "group": existing.group,
                                  "action": action.action, "style": existing.style, "target": existing.target})
            if action.action_id:
                self.seen_action_ids.add(action.action_id)
            return ActionReceipt(action=action.action, target=action.target, style=action.style,
                                 group=existing.group, result="idempotent_noop" if unchanged else "accepted")

        if action.action == "combat":
            requested = {
                str(name): int(count) for name, count in sorted((action.units or {}).items())
            }
            # Identical active combat is a no-op (dedupe).
            for demand in self.demands.values():
                if (
                    demand.is_active
                    and demand.action == "combat"
                    and demand.style == action.style
                    and demand.target == action.target
                    and dict(sorted((demand.units or {}).items())) == requested
                ):
                    if action.action_id:
                        self.seen_action_ids.add(action.action_id)
                    return ActionReceipt(
                        action="combat",
                        target=action.target,
                        style=action.style,
                        result="idempotent_noop",
                        reason="identical_combat_active",
                        action_id=action.action_id,
                        details={"requested": requested},
                        group=demand.group,
                    )
            available = {name: int(idle_army.get(name, 0)) for name in requested}
            missing = {
                name: max(0, need - available.get(name, 0))
                for name, need in requested.items()
                if need > available.get(name, 0)
            }
            if missing:
                if action.action_id:
                    self.seen_action_ids.add(action.action_id)
                details = {
                    "requested": requested,
                    "available": available,
                    "missing": missing,
                }
                garrison = {
                    name: int(count)
                    for name, count in dict(bunker_garrison or {}).items()
                    if int(count) > 0 and name in missing
                }
                if garrison:
                    details["bunker_garrison"] = garrison
                return ActionReceipt(
                    action="combat",
                    target=action.target,
                    style=action.style,
                    result="rejected",
                    reason="insufficient_units",
                    action_id=action.action_id,
                    details=details,
                )
            # Reserve idle counts for later actions in the same batch.
            for name, need in requested.items():
                idle_army[name] = max(0, int(idle_army.get(name, 0)) - need)

        # build / train append; research creates one demand; one-shot abilities too.
        baseline = int(baselines.get(action.identity(), 0))
        demand = Demand.from_action(
            action,
            order_index=self.order_counter,
            game_time=game_time,
            baseline_owned=baseline,
        )
        if action.action == "scout":
            from sc2bench_env.interface.action_catalog import get_target
            spec = get_target("scout", race=self.race)
            if spec is not None and spec.prerequisites:
                demand.target = spec.prerequisites[0]
        if action.action == "combat":
            self.group_counter += 1
            demand.group = f"group_{self.group_counter}"
            # Binding succeeds immediately; backend keeps the mission active.
            demand.state = DemandState.IN_PROGRESS
            demand.produced = 1
        self.order_counter += 1
        self.demands[demand.demand_id] = demand
        if action.action_id:
            self.seen_action_ids.add(action.action_id)
        event = {
            "type": "demand_accepted",
            "action": demand.action,
            "target": demand.target,
            "count": demand.count,
            **({"to": demand.to} if demand.to else {}),
            **({"route": route_payload(demand.route)} if demand.route else {}),
            **({"style": demand.style} if demand.style else {}),
            **({"units": dict(demand.units)} if demand.units else {}),
            **({"group": demand.group} if demand.group else {}),
        }
        self._push_event(event)
        return ActionReceipt(
            action=action.action,
            target=action.target,
            count=action.count,
            result="accepted",
            action_id=action.action_id,
            style=action.style,
            details={"requested": dict(action.units)} if action.action == "combat" else None,
            group=demand.group,
        )

    def _cancel_waiting(self, *, target_action: str, target: str, game_time: float) -> int:
        """Clear not-yet-started work for matching demands.

        Fully waiting demands are cancelled. Partially started train demands keep
        already produced units and reported in-flight units; leftover count is
        dropped without refunding or cancelling SC2 production.
        """
        cleared = 0
        for demand in list(self.demands.values()):
            if demand.action != target_action or demand.target != target:
                continue
            if not demand.is_active:
                continue

            in_flight = (demand.in_flight if demand.in_flight is not None
                         else int(demand.state == DemandState.IN_PRODUCTION))
            if demand.action == "train" and demand.remaining > 0 and (demand.produced > 0 or in_flight > 0):
                keep = min(demand.count, demand.produced + in_flight)
                if demand.count > keep:
                    cleared += demand.count - keep
                    demand.count = keep
                    demand.updated_at = game_time
                    self._push_event(
                        {
                            "type": "demand_trimmed",
                            "action": demand.action,
                            "target": demand.target,
                            "kept": keep,
                            "produced": demand.produced,
                        }
                    )
                if demand.produced >= demand.count:
                    demand.mark_completed(game_time)
                    self._push_event(
                        {
                            "type": "train_completed",
                            "target": demand.target,
                            "count": demand.count,
                        }
                    )
                continue

            if demand.state == DemandState.WAITING_TO_START and demand.is_cancellable:
                units = max(1, demand.remaining)
                demand.mark_cancelled(game_time)
                cleared += units
                self._push_event(
                    {
                        "type": "demand_cancelled",
                        "action": demand.action,
                        "target": demand.target,
                    }
                )
        return cleared

    def apply_updates(self, updates: Iterable[DemandUpdate], *, game_time: float) -> None:
        merged_by_id: Dict[str, DemandUpdate] = {}
        buckets: Dict[tuple[str, str], List[DemandUpdate]] = {}
        for update in updates:
            if update.demand_id:
                existing = merged_by_id.get(update.demand_id)
                if existing is None:
                    merged_by_id[update.demand_id] = DemandUpdate(
                        demand_id=update.demand_id,
                        action=update.action,
                        target=update.target,
                        produced_delta=update.produced_delta or update.completed_delta,
                        state=update.state or update.status,
                        waiting_for=update.waiting_for,
                        failure_reason=update.failure_reason,
                        end_reason=update.end_reason,
                        completed_delta=update.completed_delta,
                        in_progress=update.in_progress,
                        status=update.status,
                    )
                else:
                    delta = update.produced_delta or update.completed_delta
                    existing.produced_delta += max(0, delta)
                    if update.failure_reason:
                        existing.failure_reason = update.failure_reason
                    if update.end_reason:
                        existing.end_reason = update.end_reason
                    if update.state or update.status:
                        existing.state = update.state or update.status
                    if update.waiting_for is not None:
                        existing.waiting_for = update.waiting_for
                    if update.in_progress is not None:
                        existing.in_progress = update.in_progress
                continue
            if update.action and update.target is not None:
                buckets.setdefault((update.action, update.target), []).append(update)

        for demand in list(self.demands.values()):
            if not demand.is_active:
                continue
            update = merged_by_id.get(demand.demand_id)
            if update is None:
                bucket = buckets.get(demand.identity())
                update = bucket.pop(0) if bucket else None
            if update is None:
                continue
            self._apply_one(demand, update, game_time=game_time)

    def _apply_one(self, demand: Demand, update: DemandUpdate, *, game_time: float) -> None:
        failure = update.failure_reason
        state = update.state or update.status
        if failure or state == DemandState.FAILED:
            demand.mark_failed(failure or "backend_failed", game_time)
            self._push_event(
                {
                    "type": "demand_failed",
                    "action": demand.action,
                    "target": demand.target,
                    "reason": demand.failure_reason,
                    **({"style": demand.style} if demand.style else {}),
                }
            )
            return

        if update.end_reason and demand.action == "combat":
            demand.end_reason = update.end_reason
            demand.state = DemandState.COMPLETED
            demand.waiting_for = None
            demand.updated_at = game_time
            self._push_event(
                {
                    "type": "combat_ended",
                    "group": demand.group,
                    "style": demand.style,
                    "target": demand.target,
                    "units": dict(demand.units or {}),
                    "end_reason": update.end_reason,
                }
            )
            return

        produced_delta = update.produced_delta or update.completed_delta
        if produced_delta:
            demand.produced = min(demand.count, demand.produced + max(0, produced_delta))
        if demand.action == "train" and update.in_progress is not None:
            demand.in_flight = max(0, int(update.in_progress))

        if update.waiting_for is not None:
            demand.waiting_for = update.waiting_for
        elif state in {DemandState.WAITING_TO_START, DemandState.IN_PRODUCTION,
                       DemandState.IN_PROGRESS, DemandState.COMPLETED}:
            # A fresh waiting-state refresh with no known blocker must not keep
            # yesterday's resource/prerequisite explanation alive indefinitely.
            demand.waiting_for = None

        if demand.action == "build" and demand.produced >= demand.count:
            # Build action ends when the unfinished entity appears.
            demand.mark_completed(game_time)
            self._push_event(
                {
                    "type": "build_started",
                    "target": demand.target,
                }
            )
            return
        elif demand.action == "train" and demand.produced >= demand.count:
            demand.mark_completed(game_time)
            self._push_event(
                {
                    "type": "train_completed",
                    "target": demand.target,
                    "count": demand.count,
                }
            )
            return
        elif state in ACTIVE_DEMAND_STATES:
            # A merged update can contain the final birth plus an idle producer
            # refresh. Actual completion above wins over that transient state,
            # so its completion event is not silently lost.
            if (
                state == DemandState.WAITING_TO_START
                and demand.action != "train"
                and demand.state
                in {
                    DemandState.IN_PRODUCTION,
                    DemandState.WORKER_EN_ROUTE,
                    DemandState.UNDER_CONSTRUCTION,
                    DemandState.IN_PROGRESS,
                }
            ):
                # Backend waiting-reason refresh must not regress execution state.
                pass
            else:
                demand.state = state
        elif demand.action == "research" and (
            state == DemandState.COMPLETED or demand.produced >= 1
        ):
            demand.mark_completed(game_time)
            self._push_event({"type": "research_queued", "target": demand.target})
            return
        elif demand.action in {
            "scan", "call_mule", "chrono_boost", "inject_larva", "spawn_creep_tumor", "upgrade", "scout",
        } and (
            state == DemandState.COMPLETED or demand.produced >= 1
        ):
            demand.mark_completed(game_time)
            event: dict = {"type": demand.action, "target": demand.target}
            if demand.to:
                event["to"] = demand.to
            if demand.route:
                event["route"] = route_payload(demand.route)
            self._push_event(event)
            return
        elif demand.action == "combat" and state == DemandState.COMPLETED:
            demand.end_reason = demand.end_reason or "completed"
            demand.mark_completed(game_time)
            self._push_event(
                {
                    "type": "combat_ended",
                    "style": demand.style,
                    "target": demand.target,
                    "units": dict(demand.units or {}),
                    "end_reason": demand.end_reason,
                }
            )
            return
        elif update.in_progress and update.in_progress > 0:
            if demand.action == "build":
                demand.state = DemandState.UNDER_CONSTRUCTION
            elif demand.action == "train":
                demand.state = DemandState.IN_PRODUCTION
            elif demand.action == "research":
                demand.state = DemandState.IN_PROGRESS
            else:
                demand.state = DemandState.IN_PROGRESS
        elif demand.produced <= 0:
            demand.state = DemandState.WAITING_TO_START

        demand.updated_at = game_time

    def building_summary(self, completed_buildings: Dict[str, int], under_construction: Dict[str, int]) -> Dict[str, dict]:
        """Agent-visible Building section aggregated by type."""
        types = set(completed_buildings) | set(under_construction)
        waiting: Dict[str, int] = {}
        en_route: Dict[str, int] = {}
        waiting_for: Dict[str, Optional[str]] = {}
        for demand in self.active_demands():
            if demand.action != "build":
                continue
            types.add(demand.target)
            if demand.state == DemandState.WAITING_TO_START:
                waiting[demand.target] = waiting.get(demand.target, 0) + demand.remaining
                if demand.waiting_for:
                    waiting_for[demand.target] = demand.waiting_for
            elif demand.state == DemandState.WORKER_EN_ROUTE:
                en_route[demand.target] = en_route.get(demand.target, 0) + demand.remaining

        summary: Dict[str, dict] = {}
        for building_type in sorted(types):
            row = {
                "completed": int(completed_buildings.get(building_type, 0)),
                "under_construction": int(under_construction.get(building_type, 0)),
                "worker_en_route": int(en_route.get(building_type, 0)),
                "waiting_to_start": int(waiting.get(building_type, 0)),
            }
            if waiting_for.get(building_type):
                row["waiting_for"] = waiting_for[building_type]
            if any(value for key, value in row.items() if key != "waiting_for"):
                summary[building_type] = row
        return summary

    @staticmethod
    def _cancellable_count(demand: Demand) -> Optional[int]:
        """Project the cancel boundary; unknown paid queues are not guessed."""
        if not demand.is_active:
            return 0
        if demand.action == "train" and demand.remaining > 0:
            if demand.in_flight is None and demand.state != DemandState.WAITING_TO_START:
                return None
            queued = demand.in_flight or 0
            if demand.produced > 0 or queued > 0:
                return max(0, demand.remaining - queued)
        if demand.state == DemandState.WAITING_TO_START and demand.is_cancellable:
            return max(1, demand.remaining)
        return 0

    def production_priority_summary(self) -> List[dict]:
        """Relative active macro order; no internal IDs or reordered buckets."""
        rows = []
        for demand in self.active_demands():
            if demand.action not in {"build", "train", "research"}:
                continue
            row = {"action": demand.action, "target": demand.target,
                   "state": demand.state.value, "remaining": demand.remaining,
                   "cancellable_count": self._cancellable_count(demand)}
            if demand.action == "train":
                queued = (demand.in_flight if demand.in_flight is not None else
                          (0 if demand.state == DemandState.WAITING_TO_START else None))
                if queued is not None:
                    queued = min(demand.remaining, max(0, queued))
                row.update({"order_progress": f"{demand.produced}/{demand.count}",
                            "in_production": queued,
                            "waiting_to_produce": max(0, demand.remaining - queued) if queued is not None else None})
            if demand.waiting_for:
                row["waiting_for"] = demand.waiting_for
            rows.append(row)
        return rows

    def training_summary(self, in_production: Dict[str, int]) -> Dict[str, dict]:
        # Living stock belongs to own_forces. Training reports only production
        # activity so the same inventory is not repeated in two sections.
        types = set(in_production)
        progress: Dict[str, List[str]] = {}
        waiting: Dict[str, int] = {}
        waiting_for: Dict[str, str] = {}
        for demand in self.active_demands():
            if demand.action != "train":
                continue
            types.add(demand.target)
            progress.setdefault(demand.target, []).append(
                f"{demand.produced}/{demand.count}"
            )
            queued = (demand.in_flight if demand.in_flight is not None
                      else int(demand.state == DemandState.IN_PRODUCTION))
            waiting[demand.target] = waiting.get(demand.target, 0) + max(0, demand.remaining - queued)
            if demand.waiting_for:
                waiting_for[demand.target] = demand.waiting_for

        summary: Dict[str, dict] = {}
        for unit_type in sorted(types):
            row = {
                "in_production": int(in_production.get(unit_type, 0)),
                "waiting_to_produce": int(waiting.get(unit_type, 0)),
            }
            orders = progress.get(unit_type) or []
            if orders:
                # Show the oldest active order progress for readability.
                row["order_progress"] = orders[0]
            if unit_type in waiting_for:
                row["waiting_for"] = waiting_for[unit_type]
            if any(isinstance(value, int) and value for value in row.values()) or "order_progress" in row:
                summary[unit_type] = row
        return summary

    def research_summary(self, completed_upgrades: Sequence[str], in_progress: Sequence[str]) -> Dict[str, str]:
        summary: Dict[str, str] = {}
        for name in completed_upgrades:
            summary[name] = "completed"
        for name in in_progress:
            summary[name] = "in_progress"
        for demand in self.active_demands():
            if demand.action != "research":
                continue
            summary.setdefault(demand.target, "waiting_to_start")
            if demand.waiting_for:
                summary[demand.target] = f"waiting_for:{demand.waiting_for}"
        return summary

    def _push_event(self, event: dict) -> None:
        self.recent_events.append(event)
        if len(self.recent_events) > self.event_limit:
            self.recent_events = self.recent_events[-self.event_limit :]
