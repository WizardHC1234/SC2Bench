"""Episode metadata and model-session recording."""
from __future__ import annotations


import json
import os
import platform
import sys
import threading
import time
import math
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import metadata
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4
from sc2bench_env.recording.engine import CompactEngineState, validate_interval

RECORD_SCHEMA_VERSION = "0.8"
TOOL_PROTOCOL_VERSION = "tool_turn_v3"
_EXECUTION_STATE = {
    "ok": "completed",
    "staged": "staged",
    "no_op": "no_op",
    "decision_ready": "decision_ready",
    "rejected": "rejected",
}
_UNSET = object()
_CONSOLE_GUARD = threading.Lock()
_CONSOLE_SINKS: Dict[int, Dict[str, Any]] = {}
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _plain_console_text(data: Any) -> str:
    text = data if isinstance(data, str) else str(data)
    return _ANSI_ESCAPE.sub("", text)


def _attach_console_log(stream: Any, handle: Any) -> None:
    """Copy later writes on this stream into handle. The original stream still receives them."""
    with _CONSOLE_GUARD:
        sink = _CONSOLE_SINKS.get(id(stream))
        if sink is None:
            original = stream.write
            handles: List[Any] = []

            def write(data: Any, _original: Any = original, _handles: List[Any] = handles) -> Any:
                result = _original(data)
                text = _plain_console_text(data)
                with _CONSOLE_GUARD:
                    targets = list(_handles)
                for target in targets:
                    try:
                        target.write(text)
                        target.flush()
                    except Exception:
                        continue
                return result

            stream.write = write
            sink = {"original": original, "write": write, "handles": handles}
            _CONSOLE_SINKS[id(stream)] = sink
        sink["handles"].append(handle)


def _detach_console_log(stream: Any, handle: Any) -> None:
    with _CONSOLE_GUARD:
        sink = _CONSOLE_SINKS.get(id(stream))
        if sink is None:
            return
        handles: List[Any] = sink["handles"]
        if handle in handles:
            handles.remove(handle)
        if handles or stream.write is not sink["write"]:
            if not handles:
                _CONSOLE_SINKS.pop(id(stream), None)
            return
        stream.write = sink["original"]
        _CONSOLE_SINKS.pop(id(stream), None)


class _ConsoleLog:
    """Episode copy of text written to the process console."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any = None
        self._streams: List[Any] = []

    def start(self) -> None:
        if self._handle is not None:
            return
        self._handle = self.path.open("a", encoding="utf-8", newline="")
        streams: List[Any] = []
        for stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
            if stream is None or stream in streams or not hasattr(stream, "write"):
                continue
            streams.append(stream)
            _attach_console_log(stream, self._handle)
        self._streams = streams

    def stop(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        for stream in self._streams:
            _detach_console_log(stream, handle)
        self._streams = []
        try:
            handle.flush()
            handle.close()
        except Exception:
            return


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def episode_folder_name(config: Mapping[str, Any]) -> str:
    """Readable match metadata. The clock prefix is the machine's local time."""
    stamp = datetime.now().strftime("%y%m%d_%H%M%S")
    races = {"terran": "T", "protoss": "P", "zerg": "Z", "random": "R"}
    matchup = "{}v{}".format(
        races.get(str(config.get("race", "")).lower(), "X"),
        races.get(str(config.get("enemy_race", "")).lower(), "X"),
    )
    opponent = str(config.get("opponent", "opponent"))
    def safe_part(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_-")[:40] or "unknown"
    return "_".join((stamp, matchup, safe_part(opponent),
                     safe_part(str(config.get("map_name", "map")))))


def _json_safe(value: Any) -> Any:
    """Keep rejected non-JSON values diagnosable without emitting invalid JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"non_json_number": str(value)}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return {"non_json_type": type(value).__name__}


def collect_versions() -> Dict[str, Any]:
    versions: Dict[str, Any] = {"python": platform.python_version()}
    for package in ("sc2bench-env", "burnysc2", "sharpy-sc2"):
        try:
            dist = metadata.distribution(package)
            versions[package] = dist.version
            if package == "sharpy-sc2":
                try:
                    direct_url = json.loads(dist.read_text("direct_url.json") or "{}")
                except json.JSONDecodeError:
                    direct_url = {}
                versions["sharpy_commit"] = direct_url.get("vcs_info", {}).get("commit_id")
        except metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def _knowledge_fields() -> Dict[str, Any]:
    from sc2bench_env.catalog.knowledge import record_fields

    return record_fields()


def _latest_agent_input(agent_context: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return the last actual Observation message supplied by a harness."""
    if not isinstance(agent_context, dict):
        return None
    messages = agent_context.get("messages")
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.startswith("[Current Observation]"):
            return content
    return None


@dataclass
class TrajectoryRecorder:
    """Collect runtime events in memory and persist the model session."""

    episode_id: str = "episode"
    config: Dict[str, Any] = field(default_factory=dict)
    evaluation_interval_seconds: float = 1.0
    steps: List[Dict[str, Any]] = field(default_factory=list)
    result: Optional[str] = None
    directory: Optional[Path] = None
    summary: Optional[Dict[str, Any]] = None
    _started_at: str = field(default_factory=_utc_now)
    _started_clock: float = field(default_factory=time.perf_counter)
    _game_time: float = 0.0
    _decision_count: int = 0
    _rejected_count: int = 0
    _model: Optional[str] = None
    _session_messages: List[Any] = field(default_factory=list)
    _session_tools: Optional[List[Any]] = None
    _session_scope: Optional[str] = None
    _session_decision_index: Optional[int] = None
    _decision_sessions: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    _episode_text: str = ""
    _console_log: Optional[_ConsoleLog] = None
    _recorded_agent_call_ids: Set[str] = field(default_factory=set)
    _tool_call_records: List[Dict[str, Any]] = field(default_factory=list)
    _model_call_records: List[Dict[str, Any]] = field(default_factory=list)
    _branch_directory: Optional[Path] = None
    _branch_id: Optional[str] = None
    _writes_suspended: bool = False
    _evaluation_lock: Any = field(default_factory=threading.Lock, repr=False)
    _engine_trace: Any = field(default_factory=CompactEngineState, repr=False)

    def __post_init__(self) -> None:
        self.evaluation_interval_seconds = validate_interval(self.evaluation_interval_seconds)

    def _write_evaluation(self, output: Path, row: Dict[str, Any]) -> None:
        from sc2bench_env.recording.engine import append_engine_sample
        append_engine_sample(output / "evaluation.jsonl.gz", {
            **_json_safe(row), "episode_id": self.episode_id,
            "branch_id": self._branch_id, "recorded_at": _utc_now(),
        })

    def record_engine_sample(self, sample: Dict[str, Any]) -> None:
        """Write evaluation-only native state into the active timeline."""
        with self._evaluation_lock:
            output = self._output_directory()
            if output is None:
                return
            row = self._engine_trace.update(sample, self.evaluation_interval_seconds)
            if row is not None:
                self._write_evaluation(output, row)

    def start(
        self, root: str | Path, *, prompt: str, backend: str,
        folder_name: Optional[str] = None,
    ) -> Path:
        """Create an exclusive episode directory before starting the backend."""
        if self.directory is not None:
            raise RuntimeError("Recorder already started")
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        name = folder_name or episode_folder_name(self.config)
        suffix = 1
        while True:
            directory = root / (name if suffix == 1 else f"{name}_{suffix}")
            try:
                # Exclusive creation also handles concurrent games in the same second.
                directory.mkdir()
            except FileExistsError:
                suffix += 1
                continue
            self.directory = directory
            break
        metadata_payload = {
                "schema_version": RECORD_SCHEMA_VERSION,
                "tool_protocol_version": TOOL_PROTOCOL_VERSION,
                "episode_id": self.episode_id,
                "started_at": self._started_at,
                "backend": backend,
                "config": self.config,
                "versions": collect_versions(),
                **_knowledge_fields(),
                "timestamp_timezone": "UTC",
                "platform_prompt_char_count": len(prompt),
                "evaluation_recording": {"schema_version": "2", "mode": "compact",
                                         "interval_game_seconds": self.evaluation_interval_seconds},
            }
        self._header_prompt = prompt
        self._header_metadata = metadata_payload
        self._write_episode_header()
        self._write_session()
        self._write_metrics()
        self._console_log = _ConsoleLog(self.directory / "log.txt")
        self._console_log.start()
        return self.directory

    def bind_knowledge(self) -> None:
        """Rewrite the episode header after the live client snapshot is known."""
        if self.directory is None or self.summary is not None:
            return
        self._header_metadata.update(_knowledge_fields())
        self._write_episode_header()

    def _write_episode_header(self) -> None:
        self._episode_text = (
            "SC2Bench episode\n\n"
            + "Configuration and versions\n"
            + json.dumps(_json_safe(self._header_metadata), ensure_ascii=False, indent=2)
            + "\n\nPlatform contract\n" + self._header_prompt + "\n"
        )
        output = self._output_directory()
        if output is None:
            return
        self._write_atomic_text(output / "episode.txt", self._episode_text + "\nStatus: in progress\n")

    def stop_console_log(self) -> None:
        log = self._console_log
        if log is not None:
            log.stop()

    def __del__(self) -> None:
        try:
            self.stop_console_log()
        except Exception:
            return

    def _remember_session(
        self,
        agent_context: Optional[Dict[str, Any]],
        *,
        decision_index: Optional[int] = None,
        game_time: Optional[float] = None,
    ) -> None:
        """Keep the latest transcript and each completed decision-scoped transcript."""
        supplied = agent_context or {}
        messages = supplied.get("messages") if "messages" in supplied else None
        if isinstance(messages, list):
            self._session_messages = deepcopy(_json_safe(messages))
            system = next((
                message.get("content") for message in messages
                if isinstance(message, dict) and message.get("role") == "system"
                and isinstance(message.get("content"), str)
            ), None)
            if system is not None and system != self._header_prompt:
                self._header_prompt = system
                self._header_metadata["platform_prompt_char_count"] = len(system)
                self._write_episode_header()
        tools = supplied.get("tools") if "tools" in supplied else None
        if isinstance(tools, list):
            self._session_tools = deepcopy(_json_safe(tools))
        model = supplied.get("model")
        if isinstance(model, str) and model:
            self._model = model
        scope = supplied.get("session_scope")
        if scope in {"episode", "decision"}:
            self._session_scope = scope
        if decision_index is not None:
            self._session_decision_index = int(decision_index)
        if (
            self._session_scope == "decision"
            and isinstance(messages, list)
            and decision_index is not None
        ):
            self._decision_sessions[int(decision_index)] = {
                "schema_version": RECORD_SCHEMA_VERSION,
                "tool_protocol_version": TOOL_PROTOCOL_VERSION,
                "episode_id": self.episode_id,
                "decision_index": int(decision_index),
                "game_time_seconds": (
                    float(game_time) if game_time is not None else float(self._game_time)
                ),
                "model": self._model,
                "tools": deepcopy(_json_safe(self._session_tools)),
                "messages": deepcopy(_json_safe(self._session_messages)),
            }
            self._write_decision_sessions()
        self._write_session()

    def _output_directory(self) -> Optional[Path]:
        """Branch steps stay out of the trunk. A parked savepoint writes nowhere."""
        if self._writes_suspended:
            return None
        return self._branch_directory or self.directory

    def suspend_writes(self) -> None:
        """Keep the fork cursor in memory without rewriting the trunk files."""
        self._writes_suspended = True

    def capture_cursor(self) -> dict[str, Any]:
        """Copy the in-memory record so a later branch can start from this point."""
        with self._evaluation_lock:
            output = self._output_directory()
            pending = self._engine_trace.flush() if output is not None else None
            if pending is not None:
                self._write_evaluation(output, pending)
        return {
            "steps": deepcopy(self.steps),
            "result": self.result,
            "summary": deepcopy(self.summary),
            "game_time": self._game_time,
            "decision_count": self._decision_count,
            "rejected_count": self._rejected_count,
            "model": self._model,
            "session_messages": deepcopy(self._session_messages),
            "session_tools": deepcopy(self._session_tools),
            "session_scope": self._session_scope,
            "session_decision_index": self._session_decision_index,
            "decision_sessions": deepcopy(self._decision_sessions),
            "recorded_agent_call_ids": set(self._recorded_agent_call_ids),
            "tool_call_records": deepcopy(self._tool_call_records),
            "model_call_records": deepcopy(self._model_call_records),
            "engine_trace": deepcopy(self._engine_trace),
        }

    def restore_cursor(self, cursor: dict[str, Any]) -> None:
        """Rewind the in-memory record. The trunk files are left as they were."""
        self.steps = deepcopy(cursor["steps"])
        self.result = cursor["result"]
        self.summary = deepcopy(cursor["summary"])
        self._game_time = float(cursor["game_time"])
        self._decision_count = int(cursor["decision_count"])
        self._rejected_count = int(cursor["rejected_count"])
        self._model = cursor["model"]
        self._session_messages = deepcopy(cursor["session_messages"])
        self._session_tools = deepcopy(cursor["session_tools"])
        self._session_scope = cursor["session_scope"]
        self._session_decision_index = cursor["session_decision_index"]
        self._decision_sessions = deepcopy(cursor["decision_sessions"])
        self._recorded_agent_call_ids = set(cursor["recorded_agent_call_ids"])
        self._tool_call_records = deepcopy(cursor["tool_call_records"])
        self._model_call_records = deepcopy(cursor["model_call_records"])
        self._engine_trace = deepcopy(cursor.get("engine_trace", CompactEngineState()))
        self._branch_directory = None
        self._branch_id = None
        self._writes_suspended = False

    def open_branch(self, branch_id: str, *, metadata: Dict[str, Any]) -> Path:
        """Create records/<episode>/branches/<branch_id>/ for the restored line."""
        if self.directory is None:
            raise RuntimeError("Recorder has not started")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(branch_id)).strip("._") or "branch"
        root = self.directory / "branches"
        root.mkdir(parents=True, exist_ok=True)
        suffix = 1
        while True:
            path = root / (safe if suffix == 1 else f"{safe}_{suffix}")
            try:
                path.mkdir()
            except FileExistsError:
                suffix += 1
                continue
            break
        self._branch_directory = path
        self._branch_id = path.name
        self._engine_trace.last_emitted_at = None
        self._engine_trace.contacts = {}
        self._engine_trace.tasks = {}
        self._engine_trace.combat_groups = {}
        self._engine_trace.combat_trace_seen = False
        self._write_atomic(path / "branch.json", {
            "branch_id": path.name,
            **metadata,
        })
        self._write_episode_header()
        self._write_session()
        self._write_metrics()
        self._write_decision_sessions()
        return path

    def _write_session(self) -> None:
        output = self._output_directory()
        if output is None:
            return
        self._write_atomic(output / "session.json", {
            "schema_version": RECORD_SCHEMA_VERSION,
            "tool_protocol_version": TOOL_PROTOCOL_VERSION,
            "episode_id": self.episode_id,
            "model": self._model,
            "scope": self._session_scope,
            "decision_index": self._session_decision_index,
            "tools": self._session_tools,
            "messages": self._session_messages,
        })

    def _write_metrics(self) -> None:
        output = self._output_directory()
        if output is None:
            return
        self._write_atomic(output / "metrics.json", {
            "schema_version": RECORD_SCHEMA_VERSION,
            "tool_protocol_version": TOOL_PROTOCOL_VERSION,
            "episode_id": self.episode_id,
            "tool_calls": self._tool_call_records,
            "model_calls": self._model_call_records,
            "summary": deepcopy(self.summary) or {},
        })

    def _write_decision_sessions(self) -> None:
        """Write one self-contained transcript per decision for decision-scoped Agents."""
        output = self._output_directory()
        if output is None:
            return
        lines = [
            json.dumps(
                _json_safe(self._decision_sessions[index]),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            for index in sorted(self._decision_sessions)
        ]
        content = "\n".join(lines) + ("\n" if lines else "")
        self._write_atomic_text(output / "decision_sessions.jsonl", content)

    def record_agent_call_failure(self, agent_context: Dict[str, Any], *, game_time: float) -> None:
        """A failed external call is not a submitted/invalid environment decision."""
        if self.summary is not None:
            raise RuntimeError("Episode ended")
        recorded_at = _utc_now()
        self._game_time = float(game_time)
        self._remember_session(
            agent_context,
            decision_index=self._decision_count + 1,
            game_time=self._game_time,
        )
        supplied = agent_context.get("agent_calls")
        if not isinstance(supplied, list) or not supplied:
            supplied = [{
                "call_id": agent_context.get("call_id") or uuid4().hex,
                "role": agent_context.get("role") or "main",
                "model": agent_context.get("model"),
                "status": "failure",
                "input_tokens": None,
                "output_tokens": None,
                "latency_seconds": agent_context.get("latency_seconds"),
                "error_type": agent_context.get("api_error_type", "unknown"),
                "http_status": agent_context.get("api_http_status"),
            }]
        self.record_agent_calls({"agent_calls": supplied}, game_time=self._game_time)
        self._append({
            "type": "agent_call_failure", "recorded_at": recorded_at,
            "step_index": None, "next_decision_index": self._decision_count + 1,
            "game_time_seconds": self._game_time,
            "error_type": agent_context.get("api_error_type", "unknown"),
            "http_status": agent_context.get("api_http_status"),
            "attempt": agent_context.get("api_attempt"),
        })
        self._write_session()
        self._write_metrics()

    def record_agent_calls(self, agent_context: Optional[Dict[str, Any]], *, game_time: float) -> None:
        """Persist harness-supplied model calls. Missing tokens stay null."""
        if self.summary is not None or not isinstance(agent_context, dict):
            return
        calls = agent_context.get("agent_calls")
        if not isinstance(calls, list):
            return
        from sc2bench_env.interface.agent import normalize_agent_call

        for item in calls:
            normalized = normalize_agent_call(item)
            if normalized is None or normalized["call_id"] in self._recorded_agent_call_ids:
                continue
            self._recorded_agent_call_ids.add(normalized["call_id"])
            entry = {
                "type": "agent_call",
                "recorded_at": _utc_now(),
                "game_time_seconds": float(game_time),
                "decision_index": self._decision_count or None,
                **normalized,
            }
            self._model_call_records.append({
                key: deepcopy(value) for key, value in entry.items() if key != "type"
            })
            self._append(entry)

    @staticmethod
    def _write_atomic(path: Path, payload: Dict[str, Any]) -> None:
        TrajectoryRecorder._write_atomic_text(
            path, json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False)
        )

    @staticmethod
    def _write_atomic_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows indexers/previewers can briefly hold the destination without
        # delete sharing. Keep atomic replacement (never truncate the old JSON),
        # retry only sharing/access errors, and surface persistent failures.
        for attempt in range(6):
            try:
                temporary.replace(path)
                return
            except PermissionError as error:
                if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 5:
                    raise
                time.sleep(0.02 * (2 ** attempt))

    def _append(self, entry: Dict[str, Any]) -> None:
        if self.summary is not None:
            raise RuntimeError("Cannot append to a finalized trajectory")
        self.steps.append(json.loads(json.dumps(_json_safe(entry), ensure_ascii=False)))
        # session.json is a model transcript and metrics.json is an aggregate.
        # Preserve structured query results, task receipts and observations too.
        self._persist_event(self.steps[-1])

    def _persist_event(self, entry: Dict[str, Any]) -> None:
        from sc2bench_env.recording.engine import compact_event
        if entry.get("type") == "agent_call":
            # Already retained in metrics.json; do not duplicate provider usage.
            return
        with self._evaluation_lock:
            output = self._output_directory()
            if output is None:
                return
            if entry.get("type") == "end":
                pending = self._engine_trace.flush()
                if pending is not None:
                    self._write_evaluation(output, pending)
            self._write_evaluation(output, compact_event(entry))

    @staticmethod
    def _tool_error_code(payload: Any) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        error = data.get("error") if isinstance(data, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        return str(code) if code is not None else None

    def _remember_tool_call(
        self, *, kind: Optional[str], name: Any, status: Any,
        execution_state: str, decision_index: Optional[int], payload: Any = None,
    ) -> None:
        self._tool_call_records.append(_json_safe({
            "name": str(name or ""),
            "kind": kind,
            "status": status,
            "execution_state": execution_state,
            "error_code": self._tool_error_code(payload),
            "decision_index": decision_index,
        }))

    @property
    def decision_count(self) -> int:
        return self._decision_count

    def record_tool_event(
        self, *, kind: str, call: Dict[str, Any], result: Any,
        game_time_seconds: float, decision_index: int, turn_id: str,
    ) -> None:
        payload = result.to_dict() if hasattr(result, "to_dict") else result
        status = payload.get("status") if isinstance(payload, dict) else None
        rendered_result = None
        try:
            from sc2bench_env.adapters.llm import LLMAdapter
            from sc2bench_env.interface.tools import ToolCall

            rendered_result = LLMAdapter(
                race=str(self.config.get("race") or "terran")
            ).render_tool_result(
                ToolCall(str(call.get("name") or ""), dict(call.get("arguments") or {})),
                result,
            )
        except (TypeError, ValueError):
            # Structured data remains authoritative if a custom call cannot be rendered.
            rendered_result = None
        execution_state = _EXECUTION_STATE.get(status, "completed")
        self._remember_tool_call(
            kind=kind,
            name=call.get("name"),
            status=status,
            execution_state=execution_state,
            decision_index=decision_index,
            payload=payload,
        )
        self._append({
            "type": "tool_call",
            "kind": kind,
            "status": status,
            "execution_state": execution_state,
            "name": call.get("name"),
            "arguments": dict(call.get("arguments") or {}),
            "call": call,
            "result": payload,
            "rendered_result": rendered_result,
            "data": payload.get("data") if isinstance(payload, dict) else None,
            "game_time_seconds": game_time_seconds,
            "decision_index": decision_index,
            "turn_id": turn_id,
            "next_decision_index": self._decision_count + 1,
            "recorded_at": _utc_now(),
        })

    def record_protocol_error(
        self, *, code: str, agent_context: Optional[Dict[str, Any]] = None,
        game_time_seconds: float = 0.0,
    ) -> None:
        self._remember_session(
            agent_context,
            decision_index=self._decision_count + 1,
            game_time=game_time_seconds,
        )
        self.record_agent_calls(agent_context, game_time=game_time_seconds)
        self._append({
            "type": "agent_protocol_error",
            "code": code,
            "game_time_seconds": game_time_seconds,
            "agent_context": agent_context,
            "recorded_at": _utc_now(),
        })
        self._write_session()
        self._write_metrics()

    def record_tool_call(
        self, *, name: str, arguments: Dict[str, Any], result: Dict[str, Any],
        game_time_seconds: float, elapsed_seconds: float,
    ) -> None:
        status = result.get("status") if isinstance(result, dict) else None
        execution_state = _EXECUTION_STATE.get(status, "completed")
        self._remember_tool_call(
            kind=None,
            name=name,
            status=status,
            execution_state=execution_state,
            decision_index=self._decision_count + 1,
            payload=result,
        )
        self._append({
            "type": "tool_call",
            "name": name,
            "arguments": arguments,
            "result": result,
            "status": status,
            "execution_state": execution_state,
            "game_time_seconds": game_time_seconds,
            "next_decision_index": self._decision_count + 1,
            "elapsed_seconds": elapsed_seconds,
            "recorded_at": _utc_now(),
        })

    def record_reset(self, observation: Dict[str, Any]) -> None:
        self._game_time = float((observation.get("game") or {}).get("game_time_seconds", 0))
        from sc2bench_env.interface.observations import render_brief_observation_text
        brief = render_brief_observation_text(observation)
        self._append(
            {
                "type": "reset",
                "observation": observation,
                "brief_observation": brief,
                "brief_observation_char_count": len(brief),
                "recorded_at": _utc_now(),
            }
        )

    def record_step(
        self,
        *,
        actions: List[Dict[str, Any]],
        observation: Dict[str, Any],
        feedback: Dict[str, Any],
        terminated: bool,
        info: Dict[str, Any],
        submitted_decision: Any = _UNSET,
        accepted: bool = True,
        validation_error: Optional[str] = None,
        game_time_before_seconds: Optional[float] = None,
        wall_time_seconds: float = 0.0,
        agent_context: Optional[Dict[str, Any]] = None,
        previous_observation: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._decision_count += 1
        self._rejected_count += int(not accepted)
        before = self._game_time if game_time_before_seconds is None else game_time_before_seconds
        self._game_time = float((observation.get("game") or {}).get("game_time_seconds", before))
        submitted = actions if submitted_decision is _UNSET else submitted_decision
        recorded_at = _utc_now()
        from sc2bench_env.interface.observations import render_brief_observation_text
        brief = render_brief_observation_text(
            observation, previous=previous_observation,
        )
        agent_input = _latest_agent_input(agent_context)
        trajectory_entry = {
                "type": "step",
                "step_index": self._decision_count,
                "recorded_at": recorded_at,
                "submitted_decision": submitted,
                "parsed_decision": [
                    {key: value for key, value in action.items() if key != "action_id"}
                    for action in actions
                ],
                "validation": {"accepted": accepted, "error": validation_error},
                "game_time_before_seconds": before,
                "game_time_after_seconds": self._game_time,
                "wall_time_seconds": wall_time_seconds,
                "observation_char_count": len(json.dumps(observation, ensure_ascii=False, separators=(",", ":"))),
                "observation": observation,
                "brief_observation": brief,
                "brief_observation_char_count": len(brief),
                "agent_input": agent_input,
                "agent_input_char_count": len(agent_input) if agent_input is not None else None,
                "feedback": feedback,
                "terminated": terminated,
                "info": info,
            }
        self._remember_session(
            agent_context,
            decision_index=self._decision_count,
            game_time=self._game_time,
        )
        self.record_agent_calls(agent_context, game_time=self._game_time)
        self._append(trajectory_entry)
        self._write_session()
        self._write_metrics()

    def record_error(
        self, error: BaseException, *, phase: str, submitted_decision: Any = None,
        agent_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self.summary is not None:
            return
        if phase == "step":
            self._decision_count += 1
        recorded_at = _utc_now()
        error_entry = {
            "type": "error", "recorded_at": recorded_at, "phase": phase,
            "step_index": self._decision_count if phase == "step" else None,
            "submitted_decision": submitted_decision,
            "error": {"type": type(error).__name__, "message": str(error)},
        }
        if phase == "step":
            self._remember_session(
                agent_context,
                decision_index=self._decision_count,
                game_time=self._game_time,
            )
            self.record_agent_calls(agent_context, game_time=self._game_time)
        self._append(error_entry)
        self._write_session()
        self._write_metrics()

    def finalize(
        self, result: Optional[str] = None, *, status: str = "completed",
        end_reason: str = "game_ended", error: Optional[str] = None,
        observation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if self.summary is not None:
            return self.to_dict()
        if status not in {"completed", "interrupted", "failed"}:
            raise ValueError(f"Invalid recording status: {status}")
        self.result = result
        # A non-blocking game can end during agent inference, with close() as
        # the next caller operation. Record its last observed state without
        # inventing a model interaction or a submitted decision.
        if observation is not None:
            self._game_time = float(observation["game"]["game_time_seconds"])
        summary = {
            "schema_version": RECORD_SCHEMA_VERSION,
            "tool_protocol_version": TOOL_PROTOCOL_VERSION,
            "episode_id": self.episode_id,
            "status": status,
            "result": result,
            "end_reason": end_reason,
            "error": error,
            "ended_at": _utc_now(),
            "game_time_seconds": self._game_time,
            "wall_time_seconds": time.perf_counter() - self._started_clock,
            "decision_count": self._decision_count,
            "rejected_count": self._rejected_count,
        }
        end_entry = {"type": "end", **summary, "replay_saved": self._replay_saved()}
        if observation is not None:
            end_entry["observation"] = deepcopy(observation)
        self.steps.append(json.loads(json.dumps(_json_safe(end_entry), ensure_ascii=False)))
        summary["replay_saved"] = self.steps[-1]["replay_saved"]
        summary["integrity"] = self._integrity()
        self.steps[-1]["integrity"] = summary["integrity"]
        self._persist_event(self.steps[-1])
        self.summary = summary
        output = self._output_directory()
        if output is not None:
            self._write_atomic_text(
                output / "episode.txt",
                self._episode_text + "\nResult\n"
                + json.dumps(_json_safe(summary), ensure_ascii=False, indent=2) + "\n",
            )
            self._write_session()
            self._write_metrics()
        return self.to_dict()

    def _replay_saved(self) -> bool:
        if self.directory is None:
            return False
        replay = self.directory / "replay.SC2Replay"
        return replay.is_file() and replay.stat().st_size > 0

    def _integrity(self) -> Dict[str, Any]:
        errors: List[str] = []
        race = str(self.config.get("race") or "")
        system = ""
        for message in self._session_messages:
            if isinstance(message, dict) and message.get("role") == "system":
                system = str(message.get("content") or "")
                break
        if system and race:
            if f"controlling {race}" not in system.lower():
                errors.append(f"system message does not identify race {race}")
            for other in ("terran", "protoss", "zerg"):
                if other != race and f"controlling {other}" in system.lower():
                    errors.append(f"system message identifies {other} during a {race} episode")
        if self._session_tools is not None and race in {"terran", "protoss", "zerg"}:
            from sc2bench_env.interface.tools import tool_specs
            expected = [spec.name for spec in tool_specs(race)]
            recorded = []
            for tool in self._session_tools:
                function = tool.get("function") if isinstance(tool, dict) else None
                recorded.append(function.get("name") if isinstance(function, dict) else None)
            if recorded != expected:
                errors.append("session tool schema does not match the episode race")
        turns: Dict[str, List[Dict[str, Any]]] = {}
        previous_after = None
        for entry in self.steps:
            if entry.get("type") == "tool_call":
                if entry.get("status") == "not_submitted" or entry.get("execution_state") == "not_submitted":
                    errors.append("a local not_submitted call was stored as a platform tool event")
                if "result" not in entry:
                    errors.append("platform tool call is missing a result")
                turn_id = str(entry.get("turn_id") or "")
                turns.setdefault(turn_id, []).append(entry)
            if entry.get("type") == "step":
                before = entry.get("game_time_before_seconds")
                after = entry.get("game_time_after_seconds")
                if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                    if after < before:
                        errors.append("decision step game time moved backwards")
                    if previous_after is not None and before < previous_after:
                        errors.append("decision step started before the previous game time")
                    previous_after = after
        for turn_id, calls in turns.items():
            indexes = {call.get("decision_index") for call in calls}
            if len(indexes) > 1:
                errors.append(f"turn {turn_id} mixes decision indexes")
            # A rejected advance stays in the record, but only an advance that
            # actually closed the decision counts toward the one-advance rule.
            closed = [
                index for index, call in enumerate(calls)
                if call.get("name") == "advance" and call.get("execution_state") == "decision_ready"
            ]
            if len(closed) > 1:
                errors.append(f"turn {turn_id} contains more than one advance")
            productive = [
                index for index, call in enumerate(calls)
                if call.get("execution_state") != "rejected"
            ]
            if productive:
                last = calls[productive[-1]]
                if last.get("name") != "advance" or last.get("execution_state") != "decision_ready":
                    errors.append(f"turn {turn_id} does not end on advance")
            ready = [call for call in calls if call.get("execution_state") == "decision_ready"]
            if ready:
                # Action results do not repeat the complete staged call list.
                # Reconstruct it from the ordered action events in this turn.
                staged = [
                    {
                        "name": call.get("name"),
                        "arguments": dict(call.get("arguments") or {}),
                    }
                    for call in calls
                    if call.get("execution_state") == "staged"
                ]
                advance_call = {"name": "advance", "arguments": ready[-1].get("arguments") or {}}
                expected = list(staged) + [advance_call]
                step = next((
                    item for item in self.steps
                    if item.get("type") == "step" and item.get("validation", {}).get("accepted", True)
                    and item.get("step_index") == int(ready[-1].get("decision_index") or 0) + 1
                ), None)
                submitted = step.get("submitted_decision") if step else None
                if isinstance(submitted, list) and submitted != expected:
                    errors.append(f"turn {turn_id} staged actions do not match the submitted decision")
        ends = [entry for entry in self.steps if entry.get("type") == "end"]
        if not ends:
            errors.append("episode end is missing")
        elif ends[-1].get("result") != self.result:
            errors.append("episode end result does not match the episode summary")
        replay_saved = self._replay_saved()
        if ends and bool(ends[-1].get("replay_saved")) != replay_saved:
            errors.append("episode end replay state does not match the saved replay")
        return {"ok": not errors, "errors": errors}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": RECORD_SCHEMA_VERSION,
            "tool_protocol_version": TOOL_PROTOCOL_VERSION,
            "episode_id": self.episode_id,
            "config": dict(self.config),
            "steps": deepcopy(self.steps),
            "result": self.result,
            "summary": deepcopy(self.summary),
        }
