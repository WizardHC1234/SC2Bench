"""SC2Bench KnowledgeBot driven by SharpyBackend bridge state."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder

from sc2bench_env.backends.sharpy.macro import ActOngoingMacroTasks
from sc2bench_env.backends.sharpy.log_manager import BenchLogManager
from sc2bench_env.backends.sharpy.state_reader import read_snapshot

if TYPE_CHECKING:
    from sc2bench_env.backends.sharpy.backend import _Bridge

logger = logging.getLogger("sc2bench_env.backends.sharpy.bot")


class BenchBot(KnowledgeBot):
    """Runs always-on Terran tactics plus platform-submitted macro Acts."""

    def __init__(self, bridge: "_Bridge", adapter: Any, name: str = "SC2Bench"):
        super().__init__(name)
        # Scope noise filtering to this bot, not shared Sharpy/global log sinks.
        self.knowledge.log_manager = BenchLogManager()
        self.bridge = bridge
        self.adapter = adapter
        self._macro_tasks: List[Dict[str, Any]] = []
        self._replay_saved = False
        # The independent Sharpy fork has no secondary Commander recorder.
        # SC2Bench owns episode recording through TrajectoryRecorder.

    async def create_plan(self) -> BuildOrder:
        return BuildOrder(
            [
                ActOngoingMacroTasks(self._macro_tasks, race=self.adapter.race_name),
                self.adapter.create_tactics(),
            ]
        )

    async def pre_step_execute(self) -> None:
        try:
            self._sync_macro_tasks()
            self.zone_registry = self.bridge.zone_registry
            self.structure_registry = self.bridge.structure_registry
            snapshot, in_prod, in_build, in_research = read_snapshot(
                self,
                self.adapter,
                zone_registry=self.bridge.zone_registry,
                structure_registry=self.bridge.structure_registry,
            )
            decision_boundary = self.bridge.on_frame(
                snapshot=snapshot,
                in_production_units=in_prod,
                in_progress_buildings=in_build,
                in_progress_research=in_research,
                macro_errors=self._collect_macro_errors(),
                waiting_reasons={str(task["task_id"]): task["_waiting_for"]
                                 for task in self._macro_tasks if task.get("_waiting_for")},
                act_completed=self._collect_act_completed(),
                scout_progress=self._collect_scout_progress(),
                combat_progress=self._collect_combat_progress(),
            )
            if decision_boundary:
                # Non-realtime SC2 advances only when its runner steps it.
                # Keep this coroutine pending, without blocking websocket I/O.
                await asyncio.to_thread(self.bridge.wait_for_decision)
                # The agent submitted new tasks while this frame was paused.
                self._sync_macro_tasks()
            if self.bridge.should_leave():
                # Explicit leave can bypass BurnySC2's post-game replay saver.
                if self.bridge.replay_path is not None and not self._replay_saved:
                    try:
                        await self.client.save_replay(str(self.bridge.replay_path))
                        self._replay_saved = True
                    except Exception as exc:
                        logger.warning("save_replay before leave failed: %s", exc)
                try:
                    await self.client.leave()
                except Exception as exc:
                    logger.warning("client.leave failed: %s", exc)
        except Exception:
            logger.exception("pre_step_execute failed")
            raise

    async def on_end(self, game_result) -> None:
        try:
            self.bridge.on_game_end(str(game_result))
        finally:
            await super().on_end(game_result)

    def _sync_macro_tasks(self) -> None:
        specs = self.bridge.get_macro_specs()
        by_id = {task.get("task_id"): task for task in self._macro_tasks}
        desired_ids = []
        new_list: List[Dict[str, Any]] = []
        for spec in specs:
            task_id = spec["task_id"]
            meta = getattr(self.bridge, "task_meta", {}).get(task_id)
            peak = getattr(self.bridge, "peak_ready", {}).get(task_id, 0)
            if spec["action"] in {"build", "train"} and meta is not None and peak >= meta[2]:
                # Stop completed macro Acts every frame, even while the model is
                # waiting. Count-based Sharpy Acts otherwise replace casualties.
                continue
            desired_ids.append(task_id)
            existing = by_id.get(task_id)
            if existing is None:
                task = {
                    "task_id": task_id,
                    "action": spec["action"],
                    "target": spec["target"],
                    "to": spec.get("to"),
                    "style": spec.get("style"),
                    "group": spec.get("group"),
                    "withdrawing": spec.get("withdrawing", False),
                    "command_revision": spec.get("command_revision", 0),
                    "units": dict(spec.get("units") or {}),
                    "to_count": spec["to_count"],
                    "order_index": int(spec.get("order_index", 10**9)),
                    "_factory": spec["factory"],
                }
                new_list.append(task)
            else:
                existing["to_count"] = spec["to_count"]
                existing["to"] = spec.get("to")
                existing["style"] = spec.get("style")
                existing["target"] = spec["target"]
                existing["group"] = spec.get("group")
                existing["withdrawing"] = spec.get("withdrawing", False)
                existing["command_revision"] = spec.get("command_revision", 0)
                existing["units"] = dict(spec.get("units") or {})
                existing["order_index"] = int(spec.get("order_index", 10**9))
                existing["_factory"] = spec["factory"]
                # Re-enable if a previous transient error was cleared by resubmit.
                if existing.get("_disabled") and not existing.get("_error", "").startswith("instantiate"):
                    existing["_disabled"] = False
                    existing.pop("_error", None)
                new_list.append(existing)
        for task in self._macro_tasks:
            if task.get("task_id") not in desired_ids and task.get("action") == "combat":
                act = task.get("_act")
                if act is not None and getattr(act, "_bound", False):
                    # A failed/removed demand must not leave permanent unit locks.
                    act._release(getattr(act, "end_reason", None) or "cancelled")
        self._macro_tasks[:] = new_list

    def _collect_macro_errors(self) -> Dict[str, str]:
        errors: Dict[str, str] = {}
        for task in self._macro_tasks:
            err = task.get("_execution_error") or task.get("_error")
            if err:
                errors[str(task["task_id"])] = str(err)
        return errors

    def _collect_act_completed(self) -> Dict[str, int]:
        completed: Dict[str, int] = {}
        for task in self._macro_tasks:
            task_id = task.get("task_id")
            if not task_id:
                continue
            if task.get("action") in {
                "scan", "call_mule", "chrono_boost", "inject_larva", "spawn_creep_tumor", "upgrade", "scout",
            } and task.get(
                "_act_done"
            ) and not (task.get("_execution_error") or task.get("_error")):
                completed[str(task_id)] = 1
            if task.get("action") == "combat":
                act = task.get("_act")
                if act is not None and getattr(act, "end_reason", None):
                    completed[str(task_id)] = 1
        return completed

    def _collect_scout_progress(self) -> Optional[Dict[str, Any]]:
        for task in self._macro_tasks:
            if task.get("action") != "scout" or task.get("_disabled"):
                continue
            act = task.get("_act")
            if act is None:
                continue
            route = [str(item) for item in (getattr(act, "route", None) or [])]
            moving_to = getattr(act, "current_zone", None)
            index = int(getattr(act, "_index", 0) or 0)
            assigned = getattr(act, "_scout_tag", None) is not None
            return {
                "route": route,
                "moving_to": str(moving_to) if moving_to else None,
                "waypoint_index": index,
                "assigned": bool(assigned),
                "done": bool(getattr(act, "_done", False)),
            }
        return None

    def _collect_combat_progress(self) -> Optional[Dict[str, Any]]:
        progress: Dict[str, Any] = {}
        for task in self._macro_tasks:
            if task.get("action") != "combat" or task.get("_disabled"):
                continue
            task_id = task.get("task_id")
            if not task_id:
                continue
            act = task.get("_act")
            requested = dict(task.get("units") or {})
            alive = dict(getattr(act, "alive_counts", requested) if act is not None else requested)
            row: Dict[str, Any] = {
                "style": task.get("style"),
                "target": task.get("target"),
                "requested": requested,
                "alive": {str(k): int(v) for k, v in alive.items()},
                "assigned": bool(getattr(act, "_bound", False)) if act is not None else False,
            }
            if act is not None:
                from sc2bench_env.backends.sharpy.combat_observation import observe_group_activity

                member_tags = set(getattr(act, "_tags", []))
                cargo_tags = {p.tag for u in self.units if u.tag in member_tags
                              for p in getattr(u, "passengers", [])}
                members = [u for u in self.units if u.tag in member_tags and u.tag not in cargo_tags]
                row.update(observe_group_activity(members, self.enemy_units, self.enemy_structures))
                positions = [u.position for u in members]
                if positions:
                    from sc2.position import Point2
                    center = Point2((sum(p.x for p in positions) / len(positions),
                                     sum(p.y for p in positions) / len(positions)))
                    registry = getattr(self, "zone_registry", None)
                    candidates = [(z, registry.center_for(z)) for z in registry.zone_ids] if registry else []
                    candidates = [(z, Point2(p)) for z, p in candidates if p is not None]
                    row["nearest_zone"] = min(candidates, key=lambda item: center.distance_to(item[1]))[0] if candidates else None
                phase = getattr(act, "phase", None)
                if phase:
                    # Internal fight is a control branch, not observed contact.
                    row["phase"] = "executing" if phase == "fight" else str(phase)
                row["cloaked"] = dict(getattr(act, "cloaked_counts", {}))
                row["forms"] = {
                    str(k): int(v)
                    for k, v in dict(getattr(act, "form_counts", {}) or {}).items()
                    if int(v) > 0
                }
                row["skill_evidence"] = {
                    str(k): int(v)
                    for k, v in dict(getattr(act, "skill_evidence", {}) or {}).items()
                    if int(v) > 0
                }
                row["transport"] = {
                    "loaded_units": dict(getattr(act, "loaded_counts", {})),
                    "activity": str(getattr(act, "transport_activity", "support")),
                    "peak_loaded_units": int(getattr(act, "peak_loaded_units", 0)),
                    "drop_unloaded": bool(getattr(act, "drop_unloaded", False)),
                }
                end_reason = getattr(act, "end_reason", None)
                if end_reason:
                    row["end_reason"] = str(end_reason)
            progress[str(task_id)] = row
        return progress or None
