"""Standard interaction trajectory recording."""

from __future__ import annotations

import json
import hashlib
import os
import platform
import time
import math
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import metadata
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Dict, List, Optional

TRAJECTORY_SCHEMA_VERSION = "0.5"
_UNSET = object()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _episode_folder_name(config: Mapping[str, Any]) -> str:
    """Readable match metadata, without exposing the internal episode UUID."""
    stamp = datetime.now(timezone.utc).strftime("%y%m%d_%H%M%S")
    races = {"terran": "T", "protoss": "P", "zerg": "Z", "random": "R"}
    matchup = "{}v{}".format(
        races.get(str(config.get("race", "")).lower(), "X"),
        races.get(str(config.get("enemy_race", "")).lower(), "X"),
    )
    opponent = str(config.get("opponent", "opponent"))
    if opponent.startswith("builtin_"):
        opponent = opponent[len("builtin_"):]
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


def _versions() -> Dict[str, Any]:
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


@dataclass
class TrajectoryRecorder:
    """Collects reset/step/close events into a fixed JSON structure."""

    episode_id: str = "episode"
    config: Dict[str, Any] = field(default_factory=dict)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    result: Optional[str] = None
    directory: Optional[Path] = None
    summary: Optional[Dict[str, Any]] = None
    _started_at: str = field(default_factory=_utc_now)
    _started_clock: float = field(default_factory=time.perf_counter)
    _game_time: float = 0.0
    _decision_count: int = 0
    _rejected_count: int = 0
    _interactions: List[Dict[str, Any]] = field(default_factory=list)
    _system_messages: Dict[str, str] = field(default_factory=dict)
    _episode_text: str = ""

    def start(self, root: str | Path, *, prompt: str, backend: str) -> Path:
        """Create an exclusive episode directory before starting the backend."""
        if self.directory is not None:
            raise RuntimeError("Recorder already started")
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        name = _episode_folder_name(self.config)
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
                "schema_version": TRAJECTORY_SCHEMA_VERSION,
                "episode_id": self.episode_id,
                "started_at": self._started_at,
                "backend": backend,
                "config": self.config,
                "versions": _versions(),
                "timestamp_timezone": "UTC",
            }
        self._episode_text = (
            "SC2Bench episode\n\n"
            + "Configuration and versions\n"
            + json.dumps(_json_safe(metadata_payload), ensure_ascii=False, indent=2)
            + "\n\nPlatform prompt\n" + prompt + "\n"
        )
        prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        self._system_messages[prompt_digest] = prompt
        self._write_atomic_text(self.directory / "episode.txt", self._episode_text + "\nStatus: in progress\n")
        (self.directory / "interactions.jsonl").touch(exist_ok=False)
        self._append({"type": "episode_start", **metadata_payload,
                      "platform_prompt_char_count": len(prompt),
                      "platform_prompt_sha256": prompt_digest},
                     keep_in_memory=False)
        return self.directory

    def _record_interaction(
        self, *, step_index: Optional[int], recorded_at: str, submitted_decision: Any,
        agent_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        supplied = agent_context or {}
        interaction = {
            "step_index": step_index,
            "recorded_at": recorded_at,
            "input": {
                "source": "agent" if "messages" in supplied else "not_supplied",
                "messages": supplied.get("messages"),
            },
            "output": {
                "assistant_content": supplied.get("assistant_content"),
                "submitted_decision": submitted_decision,
            },
        }
        if "text_observation" in supplied:
            interaction["input"]["text_observation"] = supplied["text_observation"]
        if "messages_transcript" in supplied:
            interaction["messages_transcript"] = supplied["messages_transcript"]
        # Keep usage/model/other harness metadata once, without recopying messages/output.
        extra = {
            key: value for key, value in supplied.items()
            if key not in {"messages", "assistant_content", "text_observation", "messages_transcript"}
        }
        if extra:
            interaction["metadata"] = extra
        frozen = deepcopy(_json_safe(interaction))
        self._interactions.append(frozen)
        return frozen

    def _compact_interaction(self, interaction: Dict[str, Any]) -> Dict[str, Any]:
        """Store exact repeated system content once; keep per-call message order."""
        compact = deepcopy(interaction)
        for messages in (compact.get("input", {}).get("messages"),
                         compact.get("messages_transcript")):
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, dict) or message.get("role") != "system":
                    continue
                content = message.get("content")
                if not isinstance(content, str):
                    continue
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if digest not in self._system_messages:
                    self._system_messages[digest] = content
                    self._append({"type": "system_message", "id": digest, "content": content},
                                 keep_in_memory=False)
                message.pop("content")
                message["content_ref"] = digest
        return compact

    def record_agent_call_failure(self, agent_context: Dict[str, Any], *, game_time: float) -> None:
        """A failed external call is not a submitted/invalid environment decision."""
        if self.summary is not None:
            raise RuntimeError("Episode ended")
        recorded_at = _utc_now()
        self._game_time = float(game_time)
        interaction = self._record_interaction(step_index=None, recorded_at=recorded_at,
                                               submitted_decision=None, agent_context=agent_context)
        interaction["type"] = "agent_call_failure"
        self._append({
            "type": "agent_call_failure", "recorded_at": recorded_at,
            "step_index": None, "next_decision_index": self._decision_count + 1,
            "game_time_seconds": self._game_time,
            "error_type": agent_context.get("api_error_type", "unknown"),
            "http_status": agent_context.get("api_http_status"),
            "attempt": agent_context.get("api_attempt"),
            "agent_interaction": self._compact_interaction(interaction),
        }, memory_entry={
            "type": "agent_call_failure", "recorded_at": recorded_at,
            "step_index": None, "next_decision_index": self._decision_count + 1,
            "game_time_seconds": self._game_time,
            "error_type": agent_context.get("api_error_type", "unknown"),
            "http_status": agent_context.get("api_http_status"),
            "attempt": agent_context.get("api_attempt"),
        })

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

    def _append(self, entry: Dict[str, Any], *, keep_in_memory: bool = True,
                memory_entry: Optional[Dict[str, Any]] = None) -> None:
        if self.summary is not None:
            raise RuntimeError("Cannot append to a finalized trajectory")
        line = json.dumps(_json_safe(entry), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if self.directory is not None:
            with (self.directory / "interactions.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        if keep_in_memory:
            # Preserve the public in-memory trajectory shape, without duplicating
            # full agent inputs in this second copy.
            self.steps.append(json.loads(json.dumps(_json_safe(
                entry if memory_entry is None else memory_entry), ensure_ascii=False)))

    def record_reset(self, observation: Dict[str, Any]) -> None:
        self._game_time = float((observation.get("game") or {}).get("game_time_seconds", 0))
        self._append(
            {
                "type": "reset",
                "observation": observation,
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
    ) -> None:
        self._decision_count += 1
        self._rejected_count += int(not accepted)
        before = self._game_time if game_time_before_seconds is None else game_time_before_seconds
        self._game_time = float((observation.get("game") or {}).get("game_time_seconds", before))
        submitted = actions if submitted_decision is _UNSET else submitted_decision
        recorded_at = _utc_now()
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
                "feedback": feedback,
                "terminated": terminated,
                "info": info,
            }
        interaction = self._record_interaction(
            step_index=self._decision_count, recorded_at=recorded_at,
            submitted_decision=submitted, agent_context=agent_context,
        )
        self._append({**trajectory_entry,
                      "agent_interaction": self._compact_interaction(interaction)},
                     memory_entry=trajectory_entry)

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
            interaction = self._record_interaction(
                step_index=self._decision_count, recorded_at=recorded_at,
                submitted_decision=submitted_decision, agent_context=agent_context,
            )
            self._append({**error_entry,
                          "agent_interaction": self._compact_interaction(interaction)},
                         memory_entry=error_entry)
        else:
            self._append(error_entry)

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
            "schema_version": TRAJECTORY_SCHEMA_VERSION,
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
        end_entry = {"type": "end", **summary}
        if observation is not None:
            end_entry["observation"] = deepcopy(observation)
        self._append(end_entry)
        if self.directory is not None:
            self._write_atomic_text(
                self.directory / "episode.txt",
                self._episode_text + "\nResult\n"
                + json.dumps(_json_safe(summary), ensure_ascii=False, indent=2) + "\n",
            )
        self.summary = summary
        return self.to_dict()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": TRAJECTORY_SCHEMA_VERSION,
            "episode_id": self.episode_id,
            "config": dict(self.config),
            "steps": deepcopy(self.steps),
            "result": self.result,
            "summary": deepcopy(self.summary),
        }

    def write_json(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._write_atomic(target, self.to_dict())
