"""Run the external Codex agent over a Suite.

The episode grid lives in benchmarks/tvt_hard_styles_20.json. Model and
reasoning effort come from examples/match_vs_builtin.json. At most --parallel
matches run at once.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sc2bench_env.benchmark import BenchmarkSuite
from sc2bench_env.external.codex_quota import codex_quota_exhausted


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = ROOT / "examples" / "match_vs_builtin.json"
DEFAULT_SUITE = ROOT / "benchmarks" / "tvt_hard_styles_20.json"
PLAYED = {"game_ended", "time_limit"}
RETRY_LIMIT = 3


def _load_base(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict) or not isinstance(document.get("match"), dict):
        raise ValueError("base config must be a match object")
    return document


def build_jobs(base: dict[str, Any], suite: BenchmarkSuite) -> list[dict[str, Any]]:
    """One external match config per Suite episode. The Suite owns the grid."""
    jobs = []
    for item in suite.episode_plan():
        config = deepcopy(base)
        episode = item["config"]
        match = config["match"]
        match["race"] = episode.race
        match["enemy_race"] = episode.enemy_race
        match["map"] = episode.map_name
        match["opponent"] = episode.opponent
        match["enemy_style"] = episode.enemy_style
        match["game_time_limit_seconds"] = episode.game_time_limit_seconds
        match["max_decisions"] = suite.max_decisions
        if episode.seed is not None:
            match["seed"] = episode.seed
        jobs.append({
            "id": "{0}_{1:02d}".format(item["case_id"], item["repetition"]),
            "opponent": episode.opponent,
            "enemy_style": episode.enemy_style,
            "repeat": item["repetition"],
            "seed": episode.seed,
            "config": config,
        })
    return jobs


def _player_agent(config: dict[str, Any]) -> dict[str, Any]:
    """The external player in a match file. Builtin opponents are not the agent."""
    validation = config.get("validation") if isinstance(config.get("validation"), dict) else {}
    players = config.get("players") if isinstance(config.get("players"), list) else []
    agent: dict[str, Any] = {}
    for player in players:
        if not isinstance(player, dict) or not isinstance(player.get("agent"), dict):
            continue
        candidate = player["agent"]
        if candidate.get("adapter") == "builtin":
            continue
        agent = candidate
        break
    return {
        "adapter": agent.get("adapter"),
        "model": validation.get("model"),
        "reasoning_effort": validation.get("reasoning_effort"),
    }


def _grid_label(agent: dict[str, Any]) -> str:
    model = str(agent.get("model") or "").lower()
    if "deepseek" in model:
        return "deepseek"
    adapter = str(agent.get("adapter") or "agent")
    if adapter.startswith("codex"):
        return "codex"
    return adapter


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _finished_ids(results_path: Path) -> set[str]:
    """Matches already recorded as played. A resumed grid does not run them again."""
    if not results_path.exists():
        return set()
    done = set()
    for line in results_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("id") and row.get("end_reason") in PLAYED:
            done.add(row["id"])
    return done


def _keep_played_rows(results_path: Path) -> None:
    """Drop interrupted rows so a retry can write the replacement result."""
    if not results_path.exists():
        return
    kept = []
    for line in results_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("end_reason") in PLAYED:
            kept.append(line)
    text = ("\n".join(kept) + "\n") if kept else ""
    results_path.write_text(text, encoding="utf-8")


def _clear_episode(path: Path) -> None:
    """Remove one match directory. Codex plugin clones leave an unreadable .git."""
    if not path.exists():
        return
    resolved = path.resolve()
    if "grids" not in resolved.parts or "episodes" not in resolved.parts:
        raise RuntimeError("refusing to delete {0}".format(resolved))

    def unlock(directory: Path) -> None:
        try:
            os.chmod(directory, 0o700)
            children = list(directory.iterdir())
        except OSError:
            return
        for child in children:
            if child.is_symlink():
                continue
            if child.is_dir():
                unlock(child)
            else:
                try:
                    os.chmod(child, 0o600)
                except OSError:
                    pass

    unlock(resolved)
    shutil.rmtree(resolved)


def _append_jsonl(path: Path, document: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(document, ensure_ascii=False) + "\n")


def _match_outcome(log_path: Path) -> dict[str, Any]:
    """Read the match result printed at the end of one process log."""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    decoder = json.JSONDecoder()
    found: dict[str, Any] = {}
    for index, char in enumerate(text):
        if char != "{" or (index and text[index - 1] != "\n"):
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "record_dir" in value and "end_reason" in value:
            found = value
    return found


def _stop_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    process.terminate()


def run_grid(
    jobs: list[dict[str, Any]],
    *,
    parallel: int,
    record_dir: Path,
    grid_dir: Path,
    agent: dict[str, Any],
) -> int:
    config_dir = grid_dir / "configs"
    log_dir = grid_dir / "logs"
    episodes_dir = grid_dir / "episodes"
    results_path = grid_dir / "results.jsonl"
    for job in jobs:
        _write_json(config_dir / "{0}.json".format(job["id"]), job["config"])
    _write_json(grid_dir / "plan.json", {
        "agent": agent,
        "matches": len(jobs),
        "parallel": parallel,
        "record_dir": str(record_dir),
        "episodes_dir": str(episodes_dir),
        "jobs": [
            {"id": job["id"], "opponent": job["opponent"], "enemy_style": job["enemy_style"], "repeat": job["repeat"]}
            for job in jobs
        ],
    })
    _keep_played_rows(results_path)
    finished = _finished_ids(results_path)
    pending = [job for job in jobs if job["id"] not in finished]
    for job in pending:
        _clear_episode(episodes_dir / job["id"])
    if finished:
        print("resume  kept {0} finished  remaining {1}".format(len(finished), len(pending)), flush=True)
    running: list[tuple[dict[str, Any], subprocess.Popen, Any]] = []
    attempts: dict[str, int] = {}
    failures = 0
    quota_exhausted = False

    def start(job: dict[str, Any]) -> None:
        log_path = log_dir / "{0}.txt".format(job["id"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("w", encoding="utf-8")
        command = [
            sys.executable, "-m", "sc2bench_env.benchmark", "match", "run",
            "--config", str(config_dir / "{0}.json".format(job["id"])),
            "--record-dir", str(episodes_dir / job["id"]),
        ]
        process = subprocess.Popen(command, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
        running.append((job, process, log))
        print("start {0}  pid {1}".format(job["id"], process.pid), flush=True)

    try:
        while pending or running:
            if quota_exhausted:
                pending.clear()
            while pending and len(running) < parallel:
                start(pending.pop(0))
            if not running:
                continue
            time.sleep(2)
            still_running = []
            for job, process, log in running:
                code = process.poll()
                if code is None:
                    still_running.append((job, process, log))
                    continue
                log.close()
                log_path = log_dir / "{0}.txt".format(job["id"])
                outcome = _match_outcome(log_path)
                row = {
                    "id": job["id"],
                    "opponent": job["opponent"],
                    "enemy_style": job["enemy_style"],
                    "repeat": job["repeat"],
                    "exit_code": code,
                    "result": outcome.get("result"),
                    "end_reason": outcome.get("end_reason"),
                    "error": outcome.get("error"),
                    "record_dir": outcome.get("record_dir"),
                    "codex_session": outcome.get("codex_session"),
                    "log": str(log_path),
                }
                played = code == 0 and outcome.get("end_reason") in PLAYED
                quota_hit = (
                    outcome.get("end_reason") == "codex_quota_exhausted"
                    or codex_quota_exhausted(outcome.get("error"))
                )
                if not quota_exhausted and quota_hit:
                    quota_exhausted = True
                    pending.clear()
                    print("Codex quota is exhausted; stopping the remaining matches", flush=True)
                    for _other, other_process, _other_log in running:
                        if other_process.poll() is None:
                            _stop_process_tree(other_process)
                if played or quota_exhausted:
                    _append_jsonl(results_path, row)
                    if not played:
                        failures += 1
                    print(
                        "done {0}  exit {1}  result {2}  left {3}".format(
                            job["id"], code, outcome.get("result"), len(pending) + len(still_running),
                        ),
                        flush=True,
                    )
                    continue
                attempts[job["id"]] = attempts.get(job["id"], 0) + 1
                _clear_episode(episodes_dir / job["id"])
                if attempts[job["id"]] <= RETRY_LIMIT:
                    pending.insert(0, job)
                    print(
                        "retry {0}  attempt {1}  reason {2}".format(
                            job["id"], attempts[job["id"]], outcome.get("end_reason"),
                        ),
                        flush=True,
                    )
                    continue
                _append_jsonl(results_path, row)
                failures += 1
                print(
                    "done {0}  exit {1}  result {2}  left {3}".format(
                        job["id"], code, outcome.get("result"), len(pending) + len(still_running),
                    ),
                    flush=True,
                )
            running = still_running
    except KeyboardInterrupt:
        print("stopping {0} running matches".format(len(running)), flush=True)
        for _job, process, log in running:
            _stop_process_tree(process)
            log.close()
        return 130
    print(
        "grid finished  matches {0}  failures {1}  quota_exhausted {2}  results {3}".format(
            len(jobs), failures, quota_exhausted, results_path,
        ),
        flush=True,
    )
    if quota_exhausted:
        return 3
    return 1 if failures else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the configured Suite with the external Codex agent")
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--parallel", type=int, default=10)
    parser.add_argument("--record-dir", type=Path, default=ROOT / "records")
    parser.add_argument("--grid-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="write the plan and do not start matches")
    args = parser.parse_args(argv)
    if args.parallel < 1:
        parser.error("parallel must be positive")
    suite = BenchmarkSuite.load(args.suite)
    base = _load_base(args.base)
    jobs = build_jobs(base, suite)
    agent = _player_agent(base)
    stamp = datetime.now().strftime("%y%m%d_%H%M%S")
    grid_dir = args.grid_dir or (args.record_dir / "grids" / "{0}_{1}".format(_grid_label(agent), stamp))
    print(
        "agent {0}  model {1}  suite {2}  matches {3}  parallel {4}".format(
            agent.get("adapter"), agent.get("model"), suite.to_dict()["suite_id"], len(jobs), args.parallel,
        ),
        flush=True,
    )
    if args.dry_run:
        _write_json(grid_dir / "plan.json", {
            "agent": agent,
            "matches": len(jobs),
            "jobs": [job["id"] for job in jobs],
        })
        print("plan {0}".format(grid_dir / "plan.json"), flush=True)
        return 0
    return run_grid(
        jobs, parallel=args.parallel, record_dir=args.record_dir.resolve(),
        grid_dir=grid_dir.resolve(), agent=agent,
    )


if __name__ == "__main__":
    raise SystemExit(main())
