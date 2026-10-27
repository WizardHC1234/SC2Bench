"""SC2Bench KnowledgeBot driven by SharpyBackend bridge state."""
from __future__ import annotations


import asyncio
import logging
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from sharpy.knowledges import KnowledgeBot
from sharpy.plans import BuildOrder

from sc2bench_env.backends.sharpy.macro import ActOngoingMacroTasks
from sc2bench_env.backends.sharpy.log_manager import BenchLogManager
from sc2bench_env.backends.sharpy.state_reader import read_snapshot

if TYPE_CHECKING:
    from sc2bench_env.backends.sharpy.backend import _Bridge

logger = logging.getLogger("sc2bench_env.backends.sharpy.bot")


FRAMES_PER_SECOND = 22.4


def realtime_sleep_seconds(frames: int, elapsed: float) -> float:
    """Wall-clock pause that keeps one step at real game speed."""
    return max(0.0, int(frames) / FRAMES_PER_SECOND - elapsed)


def configured_step_size(bot: Any) -> int:
    return max(1, int(bot.config["general"]["game_step_size"]))


def pin_lockstep_step(bot: Any) -> None:
    """Keep both versus clients on one step size.

    Sharpy treats a repeated game loop as realtime and drops that client to
    ``game_step = 1``. The other client stays on the configured size. A local
    two-client game then simulates two different matches: each side keeps its
    own buildings and stops seeing the opponent's army, and both can be told
    they won.
    """
    step = configured_step_size(bot)
    bot.realtime = False
    client = getattr(bot, "client", None)
    if client is not None:
        client.game_step = step
        lock_client_step(client, step)


def lock_client_step(client: Any, step: int) -> None:
    """RequestStep must use the shared size even if Sharpy mutates game_step."""
    if getattr(client, "_sc2bench_step_locked", False):
        client._sc2bench_step_size = step
        return
    original = getattr(client, "step", None)
    if not callable(original):
        return

    async def step_locked(step_size: Optional[int] = None, _original=original, _client=client):
        return await _original(int(getattr(_client, "_sc2bench_step_size", step)))

    client.step = step_locked
    client._sc2bench_step_size = step
    client._sc2bench_step_locked = True


_TOWNHALLS = frozenset({
    "command_center", "orbital_command", "planetary_fortress",
    "hatchery", "lair", "hive", "nexus",
})


def _result_name(result: Any) -> str:
    text = getattr(result, "name", None) or str(result)
    return text.split(".")[-1]


def has_townhall(snapshot: Any) -> bool:
    """True when this side still has a base in the last published frame."""
    buildings = getattr(snapshot, "buildings", None) or {}
    info = getattr(snapshot, "info", None) or {}
    under = info.get("under_construction") or {}
    for name in _TOWNHALLS:
        if int(buildings.get(name, 0) or 0) > 0 or int(under.get(name, 0) or 0) > 0:
            return True
    return False


def reconcile_versus_result(own_result: Any, all_results: Any) -> tuple[str, Optional[str]]:
    """A two-player game cannot award Victory to both sides."""
    own = str(own_result)
    if not own.startswith("Result."):
        own = f"Result.{_result_name(own_result)}"
    if not all_results or len(all_results) < 2:
        return own, None
    names = [_result_name(item) for item in all_results.values()]
    if names and all(name == "Victory" for name in names):
        return "Result.Tie", "desync"
    return own, None


def _copy_lost_units(bucket: Any) -> Dict[Any, list]:
    """Copy lost-unit lists without sharing them with the branch being replaced."""
    if not isinstance(bucket, dict):
        return {}
    copied: Dict[Any, list] = {}
    for key, units in bucket.items():
        try:
            copied[key] = deepcopy(list(units))
        except Exception:
            copied[key] = list(units)
    return copied


def reconcile_versus_group(
    rows: List[tuple[str, Optional[str], bool]],
) -> List[tuple[str, Optional[str]]]:
    """Both clients reporting Victory while both bases still exist is a desync.

    Each local result map often contains only that player's Victory, so the
    per-client check never sees the pair. A side whose protocol result is
    already Defeat keeps that loss.
    """
    if len(rows) < 2:
        return [(result, reason) for result, reason, _hall in rows]
    if any(reason == "desync" for _result, reason, _hall in rows):
        return [("Result.Tie", "desync") for _row in rows]
    names = [_result_name(result) for result, _reason, _hall in rows]
    halls = [hall for _result, _reason, hall in rows]
    if all(name == "Victory" for name in names) and all(halls):
        return [("Result.Tie", "desync") for _row in rows]
    if all(name == "Victory" for name in names) and sum(bool(hall) for hall in halls) == 1:
        return [
            ("Result.Victory", None) if hall else ("Result.Defeat", None)
            for hall in halls
        ]
    return [(result, reason) for result, reason, _hall in rows]


class BenchBot(KnowledgeBot):
    """Runs always-on Terran tactics plus platform-submitted macro Acts."""

    def __init__(self, bridge: "_Bridge", adapter: Any, name: str = "SC2Bench"):
        super().__init__(name)
        # SkeletonBot queues one worker before it can tell lockstep from realtime.
        # That spends the opening minerals on a unit the agent never ordered.
        self.realtime_worker = False
        # Scope noise filtering to this bot, not shared Sharpy/global log sinks.
        self.knowledge.log_manager = BenchLogManager()
        self.bridge = bridge
        self.adapter = adapter
        self._macro_tasks: List[Dict[str, Any]] = []
        self._replay_saved = False
        # The independent Sharpy fork has no secondary Commander recorder.
        # SC2Bench owns episode recording through TrajectoryRecorder.

    @property
    def time(self) -> float:
        """Logical match clock. quick_load restarts the raw loop at 0."""
        state = getattr(self, "state", None)
        loop = int(getattr(state, "game_loop", 0) or 0)
        offset = float(getattr(getattr(self, "bridge", None), "time_offset_seconds", 0.0) or 0.0)
        return loop / FRAMES_PER_SECOND + offset

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
            opening_frame = not self.bridge.ready.is_set()
            if opening_frame:
                # Versus side recorders attach after ready is signaled. Cache
                # their opening evidence before waking the caller.
                self._record_engine_observation(snapshot)
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
            if not opening_frame:
                self._record_engine_observation(snapshot)
            if decision_boundary:
                # Lockstep stays paused. Realtime mode keeps the clock moving at
                # 1x while the model thinks; advance itself still runs at full speed.
                if self.bridge.realtime:
                    await self._pace_realtime_until_decision()
                    self._sync_macro_tasks()
                else:
                    await self._wait_for_release()
            if self.bridge.should_leave():
                # Explicit leave can bypass BurnySC2's post-game replay saver.
                await self._save_replay_once("before leave")
                try:
                    await self.client.leave()
                except Exception as exc:
                    logger.warning("client.leave failed: %s", exc)
        except Exception:
            logger.exception("pre_step_execute failed")
            raise

    async def start_first_worker(self) -> None:
        """Keep the opening worker under the agent's production orders."""
        return

    def _record_engine_observation(self, snapshot, *, response=None) -> None:
        from sc2bench_env.recording.engine import observation_sample
        sink = getattr(self.bridge, "evaluation_sink", None)
        if sink is None and getattr(self.bridge, "last_evaluation_sample", None) is not None:
            return
        sample = observation_sample(self, snapshot, response=response)
        if sample is None:
            return
        self.bridge.last_evaluation_sample = sample
        if sink is not None:
            sink(sample)

    async def on_before_start(self):
        await super().on_before_start()
        pin_lockstep_step(self)
        ping = (await self.client.ping()).ping
        from sc2bench_env.catalog.knowledge import require_snapshot_match

        matched = require_snapshot_match(
            game_version=ping.game_version,
            data_version=ping.data_version,
            base_build=int(ping.base_build),
        )
        from sc2bench_env.catalog.knowledge import activate_game_data

        activate_game_data(matched)
        self.bridge.knowledge_snapshot = matched

    async def _after_step(self) -> int:
        # The save frame and every quick_load frame share one rule. quick_load
        # reports game_loop 0, and a second load ignores commands issued on
        # that same loop. Continuing straight from the save would otherwise
        # act one step earlier than a restored branch. Hold this frame so
        # both paths command from the next observation.
        self._hold_load_frame_actions()
        return await super()._after_step()

    def _hold_load_frame_actions(self) -> None:
        if not getattr(self, "_drop_actions_after_load", False):
            return
        self._drop_actions_after_load = False
        actions = getattr(self, "actions", None)
        if actions is not None:
            actions.clear()
        received = getattr(self, "unit_tags_received_action", None)
        if received is not None:
            received.clear()
        self._clear_issued_actions()

    def _clear_issued_actions(self) -> None:
        """Forget commands recorded for a frame whose orders were not sent."""
        action_manager = getattr(getattr(self, "knowledge", None), "action_manager", None)
        if action_manager is None:
            managers = getattr(getattr(self, "knowledge", None), "managers", None) or []
            for manager in managers:
                if manager.__class__.__name__ == "ActionManager":
                    action_manager = manager
                    break
        if action_manager is None:
            return
        stored = getattr(action_manager, "actions", None)
        if stored is not None:
            stored.clear()
        blocked = getattr(action_manager, "block_list", None)
        if blocked is not None:
            blocked.clear()

    async def on_step(self, iteration):
        # Pin before Sharpy runs. A repeated game loop must not skip this
        # client's orders or drop only this client to a one-frame step.
        pin_lockstep_step(self)
        state = getattr(self, "state", None)
        if state is not None:
            loop = int(state.game_loop)
            previous = getattr(self, "_observed_loop", None)
            if previous is not None and loop <= previous:
                # quick_load moves the loop backwards. Per-frame caches keyed by
                # that loop would otherwise replay the other branch's units.
                self._invalidate_frame_caches()
            self._observed_loop = loop
            # Sharpy skips the step when this equals the current loop. Keep it
            # behind so a quick_load that revisits a loop still issues orders.
            self.last_game_loop = loop - 1
        await super().on_step(iteration)
        pin_lockstep_step(self)

    async def _pace_realtime_until_decision(self) -> None:
        import time
        frames = max(1, int(getattr(self.client, "game_step", 1) or 1))
        while not self.bridge.advance_allowed.is_set() and not self.bridge.stopped.is_set():
            if self.bridge.should_leave():
                return
            started = time.perf_counter()
            try:
                await self.client.step(frames)
                observed = await self.client.observation()
                game_loop = int(observed.observation.observation.game_loop)
            except Exception as exc:
                logger.warning("realtime pace step failed: %s", exc)
                return
            offset = float(getattr(self.bridge, "time_offset_seconds", 0.0) or 0.0)
            self._note_paced_game_time(game_loop / FRAMES_PER_SECOND + offset)
            # Pacing already receives this message. Keep it without preparing
            # a new bot step or altering the controller's state during thinking.
            from sc2bench_env.backends.base import BackendSnapshot
            self._record_engine_observation(
                BackendSnapshot(game_time_seconds=game_loop / FRAMES_PER_SECOND + offset,
                                info={"sample_phase": "realtime_pacing",
                                      "controller_state": "not refreshed during pacing"}),
                response=observed.observation,
            )
            delay = realtime_sleep_seconds(frames, time.perf_counter() - started)
            if delay > 0:
                await asyncio.sleep(delay)

    def _note_paced_game_time(self, game_time_seconds: float) -> None:
        with self.bridge.lock:
            self.bridge.snapshot.game_time_seconds = game_time_seconds
            limit = self.bridge.max_game_time
            expired = (
                not self.bridge.snapshot.terminated and limit is not None
                and game_time_seconds >= limit
            )
            if expired:
                self.bridge.snapshot.result = "Result.Tie"
        if expired:
            self.bridge.request_leave(end_reason="time_limit")

    async def on_end(self, game_result) -> None:
        # Hold the platform result until every versus client has one. Publishing
        # the first Victory wakes that agent and freezes a double win before
        # the other client reports.
        try:
            # BurnySC2 normally saves after on_end, when SC2 may already be
            # closing its websocket. Save while this callback still owns a
            # live client; backend.py deliberately does not pass
            # save_replay_as to run_game.
            await self._save_replay_once("on game end")
            await super().on_end(game_result)
        finally:
            all_results = getattr(getattr(self, "client", None), "_game_result", None)
            self.bridge.note_raw_result(game_result, all_results)

    async def _save_replay_once(self, context: str) -> None:
        path = self.bridge.replay_path
        if path is None or self._replay_saved:
            return
        try:
            await self.client.save_replay(str(path))
            if not path.is_file() or path.stat().st_size == 0:
                raise OSError("SC2 exported an empty or missing replay")
        except Exception as exc:
            # Replay is an artifact, not the game outcome. A connection that
            # closes during replay export must never resign or invalidate the
            # already determined match result.
            logger.warning("Replay export %s failed: %s", context, exc)
        else:
            self._replay_saved = True

    async def _wait_for_release(self) -> None:
        """Stay inside this frame until the caller asks the game to advance.

        A bookmark request is handled here and does not fall through into
        on_step, so quick_save and quick_load do not spend a game frame.
        """
        while True:
            await asyncio.to_thread(self.bridge.wait_for_decision)
            request = self.bridge.take_control_request()
            if request is None:
                self._sync_macro_tasks()
                return
            try:
                await self._run_control(request)
            except Exception as exc:
                self.bridge.fail_control(request, exc)
            else:
                self.bridge.finish_control(request)

    async def _run_control(self, request: Any) -> None:
        if request.operation == "quick_save":
            await self.client.quick_save()
            # Same deferral as quick_load: this frame's new orders wait until
            # the next observation, or a restored branch acts one step later.
            self._drop_actions_after_load = True
            self._remember_ability_clock()
            self._remember_home_gather()
            self._remember_home_micro()
            self._remember_sightings()
            self._remember_engine_fingerprint(request)
            return
        if request.operation == "inspect":
            self._remember_engine_fingerprint(request)
            return
        if request.operation == "debug_end":
            await self.client.debug_leave()
            return
        if request.operation == "publish":
            # Runtime import has put the savepoint registries back. Rebuild
            # the platform snapshot from those, not from the branch just replaced.
            self._sync_macro_tasks()
            self._publish_platform_snapshot()
            return
        if request.operation == "quick_load":
            # QuickLoad stops the client's replay recorder. Keep the trunk
            # timeline, which still includes this save frame.
            await self._save_replay_once("before quick load")
            await self.client.quick_load()
            await self._refresh_after_quick_load(float(request.logical_time_seconds))
            loaded = self._current_engine_fingerprint()
            self._remember_engine_fingerprint(request, fingerprint=loaded)
            saved = self.bridge.engine_fingerprint
            if saved is not None and loaded.digest != saved.digest:
                from sc2bench_env.backends.sharpy.engine_fingerprint import describe_fingerprint_diff
                from sc2bench_env.runtime.savepoint import SavepointError

                raise SavepointError(
                    "Restored simulation does not match the savepoint:\n"
                    + describe_fingerprint_diff(saved, loaded)
                )
            return
        raise RuntimeError(f"Unknown bookmark operation {request.operation}")

    def _current_engine_fingerprint(self):
        from sc2bench_env.backends.sharpy.engine_fingerprint import fingerprint_observation

        observation = getattr(getattr(self, "state", None), "observation", None)
        if observation is None:
            raise RuntimeError("The game has no observation to fingerprint")
        return fingerprint_observation(observation)

    def _remember_engine_fingerprint(self, request: Any, fingerprint=None) -> None:
        current = fingerprint if fingerprint is not None else self._current_engine_fingerprint()
        request.fingerprint = current
        request.detail_hash = current.digest

    async def _refresh_after_quick_load(self, logical_time_seconds: float) -> None:
        """Read the loaded frame and keep logical time when raw game_loop returns to 0."""
        from s2clientprotocol import sc2api_pb2 as sc_pb
        from sc2.game_state import GameState

        state = await self.client.observation()
        game_state = GameState(state.observation)
        proto_game_info = await self.client._execute(game_info=sc_pb.RequestGameInfo())
        self._prepare_step(game_state, proto_game_info)
        raw_loop = int(game_state.game_loop)
        self.last_game_loop = raw_loop - 1
        # The load happened inside an on_step that already recorded the old
        # loop. Leaving that number in place makes every later frame look
        # like a rewind and clears the action cache until the clock catches up.
        self._observed_loop = raw_loop
        raw_time = raw_loop / FRAMES_PER_SECOND
        offset = float(logical_time_seconds) - raw_time
        with self.bridge.lock:
            self.bridge.time_offset_seconds = offset
        # Zone sightings and ability timers read ai.time. The offset is already
        # included there, so the published snapshot must not add it again.
        self._publish_platform_snapshot()
        await self._realign_after_quick_load()

    def _publish_platform_snapshot(self) -> None:
        """Publish the current frame without birth accounting."""
        snapshot, in_prod, in_build, in_research = read_snapshot(
            self,
            self.adapter,
            zone_registry=self.bridge.zone_registry,
            structure_registry=self.bridge.structure_registry,
        )
        with self.bridge.lock:
            self.bridge.snapshot = snapshot
            self.bridge.in_production_units = dict(in_prod)
            self.bridge.in_progress_buildings = dict(in_build)
            self.bridge.in_progress_research = dict(in_research)

    async def _realign_after_quick_load(self) -> None:
        """Rebuild manager caches from the loaded frame before acts run.

        Quick load happens inside this step, after Sharpy has already updated
        roles, the unit cache, and enemy memory for the branch being replaced.
        Act execution is still ahead of this hook, so those caches have to
        match the loaded units or the next orders follow the other branch.
        """
        self._drop_actions_after_load = True
        self._restore_ability_clock()
        self._restore_home_gather()
        self._restore_home_micro()
        knowledge = getattr(self, "knowledge", None)
        managers = list(getattr(knowledge, "managers", None) or [])
        if knowledge is None or not managers:
            return
        knowledge.reserved_minerals = 0
        knowledge.reserved_gas = 0
        self._drop_branch_memory()
        from sharpy.managers.extensions.custom_func_manager import CustomFuncManager

        for manager in managers:
            if isinstance(manager, CustomFuncManager):
                break
            await manager.update()

    def _cooldown_manager(self):
        knowledge = getattr(self, "knowledge", None)
        return getattr(knowledge, "cooldown_manager", None) or getattr(self, "cd_manager", None)

    def _remember_ability_clock(self) -> None:
        """Keep ability-use times from this moment. A later branch must not inherit them."""
        manager = self._cooldown_manager()
        used = getattr(manager, "used_dict", None)
        if isinstance(used, dict):
            self._saved_ability_clock = deepcopy(used)

    def _restore_ability_clock(self) -> None:
        manager = self._cooldown_manager()
        used = getattr(manager, "used_dict", None)
        if not isinstance(used, dict):
            return
        saved = getattr(self, "_saved_ability_clock", None)
        manager.used_dict = deepcopy(saved) if isinstance(saved, dict) else {}

    def _remember_home_gather(self) -> None:
        """Keep the rally already issued at this moment.

        The home gather lives for the whole game. A branch that rallies a new
        barracks would otherwise leave that tag set, and the next branch would
        skip the same building.
        """
        gather = getattr(self, "bench_home_gather", None)
        if gather is None:
            return
        point = getattr(gather, "_last_point", None)
        self._saved_home_gather = {
            "rallied_tags": set(getattr(gather, "_rallied_tags", ()) or ()),
            "point": None if point is None else (float(point.x), float(point.y)),
        }

    def _restore_home_gather(self) -> None:
        gather = getattr(self, "bench_home_gather", None)
        saved = getattr(self, "_saved_home_gather", None)
        if gather is None or not isinstance(saved, dict):
            return
        gather._rallied_tags = set(saved.get("rallied_tags") or ())
        point = saved.get("point")
        if point is None:
            gather._last_point = None
            return
        from sc2.position import Point2

        gather._last_point = Point2(point)

    def _home_micro_objects(self):
        gather = getattr(self, "bench_home_gather", None)
        rules = getattr(gather, "_micro_rules", None)
        if rules is None:
            return []
        found = []
        seen = set()
        generic = getattr(rules, "generic_micro", None)
        candidates = [generic, *list(getattr(rules, "unit_micros", {}).values())]
        for micro in candidates:
            if micro is None or id(micro) in seen:
                continue
            seen.add(id(micro))
            found.append(micro)
        return found

    def _remember_home_micro(self) -> None:
        """Keep lock, claim, and siege timers on the long-lived home micro.

        Combat acts build a fresh rules object after each load. The home
        gather does not, so a branch's cyclone lock or raven claim would
        otherwise still be there for the next branch.
        """
        saved = []
        for micro in self._home_micro_objects():
            fields = {}
            for name in ("locks", "pending_locks", "turret_points", "prepared_at", "claimed_until", "siege_status"):
                if hasattr(micro, name):
                    fields[name] = deepcopy(getattr(micro, name))
            saved.append(fields)
        self._saved_home_micro = saved

    def _restore_home_micro(self) -> None:
        saved = getattr(self, "_saved_home_micro", None)
        micros = self._home_micro_objects()
        if not isinstance(saved, list) or len(saved) != len(micros):
            return
        for micro, fields in zip(micros, saved):
            for name, value in fields.items():
                setattr(micro, name, deepcopy(value))

    def _remember_sightings(self) -> None:
        """Keep enemy tags known at this moment, including ones now in fog.

        A later load used to empty that memory. Continuing from the save still
        had it, so the same orders could pick different targets.
        """
        enemy = getattr(self, "enemy_units_manager", None)
        lost = getattr(self, "lost_units_manager", None)
        if enemy is None and lost is None:
            self._saved_sightings = None
            return
        known = getattr(enemy, "_known_enemy_units_dict", None) if enemy is not None else None
        morphed = getattr(enemy, "_morphed_type", None) if enemy is not None else None
        self._saved_sightings = {
            "known": deepcopy(known) if isinstance(known, dict) else {},
            "morphed": deepcopy(morphed) if isinstance(morphed, dict) else {},
            "cloak": bool(getattr(enemy, "_enemy_cloak_trigger", False)) if enemy is not None else False,
            "cancelled": set(getattr(lost, "_cancelled_tags", ()) or ()),
            "cancel_commanded": set(getattr(lost, "_cancel_commanded_tags", ()) or ()),
            "my_lost": _copy_lost_units(getattr(lost, "_my_lost_units", None)),
            "enemy_lost": _copy_lost_units(getattr(lost, "_enemy_lost_units", None)),
        }

    def _restore_sightings(self) -> bool:
        saved = getattr(self, "_saved_sightings", None)
        if not isinstance(saved, dict):
            return False
        enemy = getattr(self, "enemy_units_manager", None)
        lost = getattr(self, "lost_units_manager", None)
        if enemy is not None:
            enemy._known_enemy_units_dict = deepcopy(saved.get("known") or {})
            enemy._morphed_type = deepcopy(saved.get("morphed") or {})
            enemy._enemy_cloak_trigger = bool(saved.get("cloak", False))
        if lost is not None:
            lost._cancelled_tags = set(saved.get("cancelled") or ())
            lost._cancel_commanded_tags = set(saved.get("cancel_commanded") or ())
            lost._my_lost_units = _copy_lost_units(saved.get("my_lost"))
            lost._enemy_lost_units = _copy_lost_units(saved.get("enemy_lost"))
        return True

    def _drop_branch_memory(self) -> None:
        """Drop the replaced branch's assignments. Sightings return to the save."""
        roles = getattr(self, "roles", None)
        if roles is not None:
            had_task = getattr(roles, "had_task_set", None)
            if had_task is not None:
                had_task.clear()
            for role in getattr(roles, "roles", ()) or ():
                clear = getattr(role, "clear", None)
                if callable(clear):
                    clear()
        if not self._restore_sightings():
            enemy = getattr(self, "enemy_units_manager", None)
            known = getattr(enemy, "_known_enemy_units_dict", None)
            if known is not None:
                known.clear()
            morphed = getattr(enemy, "_morphed_type", None)
            if morphed is not None:
                morphed.clear()
            if enemy is not None and hasattr(enemy, "_enemy_cloak_trigger"):
                enemy._enemy_cloak_trigger = False
            lost = getattr(self, "lost_units_manager", None)
            for name in ("_my_lost_units", "_enemy_lost_units", "_cancelled_tags", "_cancel_commanded_tags"):
                bucket = getattr(lost, name, None)
                if bucket is not None:
                    bucket.clear()
        previous = getattr(self, "previous_units_manager", None)
        if previous is not None:
            previous.previous_units = {int(unit.tag): unit for unit in self.all_units}
        self._units_previous_map = {int(unit.tag): unit for unit in self.units}
        self._structures_previous_map = {int(unit.tag): unit for unit in self.structures}
        self._enemy_units_previous_map = {int(unit.tag): unit for unit in self.enemy_units}
        self._enemy_structures_previous_map = {int(unit.tag): unit for unit in self.enemy_structures}
        actions = getattr(self, "actions", None)
        if actions is not None:
            actions.clear()
        received = getattr(self, "unit_tags_received_action", None)
        if received is not None:
            received.clear()
        self._invalidate_frame_caches()

    def _invalidate_frame_caches(self) -> None:
        """Drop per-loop caches so a revisited game_loop cannot reuse old units."""
        self._generated_frame2 = -1
        for name in list(vars(self)):
            if name.startswith("_frame_") or name.startswith("_cache_"):
                delattr(self, name)
        self._clear_issued_actions()

    def _sync_macro_tasks(self) -> None:
        if self.bridge.consume_discard_macro_tasks():
            self._macro_tasks.clear()
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
                    "membership_revision": spec.get("membership_revision", 0),
                    "merge_source_ids": list(spec.get("merge_source_ids") or []),
                    "transfer_requests": [dict(item) for item in spec.get("transfer_requests") or []],
                    "merge_pending_into": spec.get("merge_pending_into"),
                    "retreat_method": spec.get("retreat_method", "move"),
                    "recall_status": spec.get("recall_status"),
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
                existing["membership_revision"] = spec.get("membership_revision", 0)
                existing["merge_source_ids"] = list(spec.get("merge_source_ids") or [])
                existing["transfer_requests"] = [
                    dict(item) for item in spec.get("transfer_requests") or []
                ]
                existing["merge_pending_into"] = spec.get("merge_pending_into")
                existing["retreat_method"] = spec.get("retreat_method", "move")
                existing["recall_status"] = spec.get("recall_status")
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
                "scan", "call_mule", "supply_drop", "chrono_boost", "inject_larva", "spawn_creep_tumor", "morph_townhall", "scout",
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
            row = {
                "route": route,
                "moving_to": str(moving_to) if moving_to else None,
                "waypoint_index": index,
                "assigned": bool(assigned),
                "done": bool(getattr(act, "_done", False)),
            }
            creep_tags = getattr(self, "bench_creep_tags", set())
            if getattr(act, "_scout_tag", None) in creep_tags:
                row["creep_generating"] = 1
            return row
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
                "style": getattr(act, "style", None) or task.get("style"),
                "target": task.get("target"),
                "requested": requested,
                "alive": {str(k): int(v) for k, v in alive.items()},
                "assigned": bool(getattr(act, "_bound", False)) if act is not None else False,
            }
            applied_transfers = task.get("_applied_transfer_ids") or set()
            if applied_transfers:
                row["applied_transfer_ids"] = sorted(str(item) for item in applied_transfers)
            if act is not None:
                from sc2bench_env.backends.sharpy.combat_observation import observe_group_activity

                member_tags = set(getattr(act, "_tags", []))
                row["member_tags"] = sorted(int(tag) for tag in member_tags)
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
                holding_since = getattr(act, "_holding_since", None)
                if holding_since is not None:
                    row["holding_since_game_time"] = float(holding_since)
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
                creep = int(getattr(act, "creep_generating", 0) or 0)
                if creep:
                    row["creep_generating"] = creep
                report = getattr(act, "recall_report", None)
                if report:
                    row["recall"] = dict(report)
                if getattr(act, "recall_failure", None):
                    row["recall_failure"] = str(act.recall_failure)
                row["transport"] = {
                    "loaded_units": dict(getattr(act, "loaded_counts", {})),
                    "activity": str(getattr(act, "transport_activity", "support")),
                    "peak_loaded_units": int(getattr(act, "peak_loaded_units", 0)),
                    "drop_unloaded": bool(getattr(act, "drop_unloaded", False)),
                }
                end_reason = getattr(act, "end_reason", None)
                if end_reason:
                    row["end_reason"] = str(end_reason)
                    transferred = dict(getattr(act, "merge_transferred_counts", {}) or {})
                    if transferred:
                        row["merge_transferred_units"] = transferred
            progress[str(task_id)] = row
        return progress or None
