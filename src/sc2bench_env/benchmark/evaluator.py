"""Read only persisted episode metadata and terminal results, never agent strategy."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable

from sc2bench_env.recording.reader import parse_episode_text


_OUTCOMES = {
    "Result.Victory": "victory",
    "Result.Defeat": "defeat",
    "Result.Tie": "tie",
}


class Evaluator:
    """First-edition objective metrics, based on episode.txt configuration and result."""

    @staticmethod
    def evaluate_batch(summary_path: str | Path) -> Dict[str, Any]:
        """Recompute finalized episode facts, preserving the source batch unchanged.

        No automatic replay/rerun and no trust in cached outcome counters.
        An unreadable record is an audit error, not a game defeat.
        """
        path = Path(summary_path).resolve()
        batch = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(batch, dict) or batch.get("schema_version") not in {"0.1", "0.2", "0.3"}:
            raise ValueError("Unsupported batch schema")
        if not isinstance(batch.get("episodes"), list):
            raise ValueError("Missing episode index")
        episodes = []
        for saved in batch["episodes"]:
            if not isinstance(saved, dict):
                raise ValueError("Invalid episode index row")
            directory = saved.get("record_directory")
            if directory is None:
                # Environment creation failures have no episode artifact to reread.
                launch_failed = (saved.get("status") == "failed" and
                                 saved.get("end_reason") in {"environment_creation_error", "worker_error"})
                row = {**saved, "status": "failed" if launch_failed else "incomplete",
                       "outcome": "unfinished", "result": None,
                       "end_reason": saved["end_reason"] if launch_failed else None,
                       "decision_count": None, "rejected_count": None,
                       "game_time_seconds": None, "wall_time_seconds": None,
                       "audit_status": "no_episode_record"}
            else:
                try:
                    if not isinstance(directory, str) or not directory:
                        raise ValueError("Invalid record_directory")
                    row = Evaluator.evaluate_episode(path.parent / directory.replace("\\", "/"))
                    if saved.get("episode_id") is not None and row["episode_id"] != saved["episode_id"]:
                        raise ValueError("Episode record does not match batch index")
                    if saved.get("config") is not None and row["config"] != saved["config"]:
                        raise ValueError("Episode configuration does not match batch index")
                    row["audit_status"] = "read"
                except (OSError, ValueError, TypeError, KeyError) as error:
                    row = {"episode_id": saved.get("episode_id"), "config": saved.get("config"),
                           "status": "failed", "outcome": "unfinished", "result": None,
                           "end_reason": "record_read_error", "decision_count": None,
                           "rejected_count": None, "game_time_seconds": None,
                           "wall_time_seconds": None, "audit_status": "record_error",
                           "audit_error_type": type(error).__name__}
                for field in ("index", "case_id", "repetition", "record_directory", "error_type",
                              "worker_pid", "worker_exitcode"):
                    if field in saved:
                        row[field] = saved[field]
                # Runtime version was supplied by Runner, not stored in episode.txt.
                if "runtime_versions" in saved:
                    row["indexed_runtime_versions"] = saved["runtime_versions"]
            episodes.append(row)
        case_ids = list(dict.fromkeys(row["case_id"] for row in episodes if "case_id" in row))
        return {
            "batch_id": batch.get("batch_id"), "source_batch": str(path),
            "source_status": batch.get("status"), "indexed_episode_count": len(episodes),
            "planned_episode_count": len(batch["planned_configs"])
            if isinstance(batch.get("planned_configs"), list) else None,
            "episodes": episodes, "aggregate": Evaluator.summarize(episodes),
            "termination_counts": Evaluator.termination_counts(episodes),
            "case_results": {case_id: {
                "aggregate": Evaluator.summarize(row for row in episodes if row.get("case_id") == case_id),
                "termination_counts": Evaluator.termination_counts(
                    row for row in episodes if row.get("case_id") == case_id),
            } for case_id in case_ids},
        }

    @staticmethod
    def evaluate_episode(record_directory: str | Path) -> Dict[str, Any]:
        directory = Path(record_directory).resolve()
        start, prompt, end = parse_episode_text(
            (directory / "episode.txt").read_text(encoding="utf-8"))
        if "episode_id" not in start or "backend" not in start or "config" not in start:
            raise ValueError("Missing episode configuration")
        status = end["status"] if end is not None else "incomplete"
        if end is not None:
            if status not in {"completed", "interrupted", "failed"}:
                raise ValueError("Invalid terminal status")
            for field in ("decision_count", "rejected_count"):
                value = end.get(field)
                if value is not None and (type(value) is not int or value < 0):
                    raise ValueError("Invalid terminal counter")
            for field in ("game_time_seconds", "wall_time_seconds"):
                value = end.get(field)
                if value is not None and (type(value) not in {int, float} or
                                          not math.isfinite(value) or value < 0):
                    raise ValueError("Invalid terminal duration")
        result = end.get("result") if end is not None else None
        return {
            "episode_id": start["episode_id"],
            "backend": start["backend"],
            "config": start["config"],
            "versions": start.get("versions"),
            "platform_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "status": status,
            "outcome": _OUTCOMES.get(result, "unfinished") if status == "completed" else "unfinished",
            "result": result if status == "completed" else None,
            "end_reason": end.get("end_reason") if end is not None else None,
            "game_time_seconds": end.get("game_time_seconds") if end is not None else None,
            "wall_time_seconds": end.get("wall_time_seconds") if end is not None else None,
            "decision_count": end.get("decision_count") if end is not None else None,
            "rejected_count": end.get("rejected_count") if end is not None else None,
        }

    @staticmethod
    def summarize(episodes: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        rows = list(episodes)
        return {
            "episodes": len(rows),
            "completed": sum(row.get("status") == "completed" for row in rows),
            "interrupted": sum(row.get("status") == "interrupted" for row in rows),
            "failed": sum(row.get("status") == "failed" for row in rows),
            "incomplete": sum(row.get("status") == "incomplete" for row in rows),
            "victory": sum(row.get("outcome") == "victory" for row in rows),
            "defeat": sum(row.get("outcome") == "defeat" for row in rows),
            "tie": sum(row.get("outcome") == "tie" for row in rows),
            "total_decisions": sum(int(row.get("decision_count") or 0) for row in rows),
            "total_rejected": sum(int(row.get("rejected_count") or 0) for row in rows),
        }

    @staticmethod
    def termination_counts(episodes: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        """Keep natural endings, timeout ties, Agent interruptions and faults separate."""
        counts: Dict[str, int] = {}
        for row in episodes:
            key = f"{row.get('status', 'incomplete')}/{row.get('end_reason') or 'unknown'}"
            counts[key] = counts.get(key, 0) + 1
        return counts
