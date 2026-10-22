"""Run the external Codex agent over a Suite.

The episode grid lives in benchmarks/tvt_hard_styles_20.json. Model and
reasoning effort come from examples/match_vs_builtin.json. At most --parallel
matches run at once.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sc2bench_env.benchmark import BenchmarkSuite


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = ROOT / "examples" / "match_vs_builtin.json"
DEFAULT_SUITE = ROOT / "benchmarks" / "tvt_hard_styles_20.json"


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


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, document: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(document, ensure_ascii=False) + "\n")


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
) -> int:
    config_dir = grid_dir / "configs"
    log_dir = grid_dir / "logs"
    results_path = grid_dir / "results.jsonl"
    for job in jobs:
        _write_json(config_dir / "{0}.json".format(job["id"]), job["config"])
    _write_json(grid_dir / "plan.json", {
        "matches": len(jobs),
        "parallel": parallel,
        "record_dir": str(record_dir),
        "jobs": [
            {"id": job["id"], "opponent": job["opponent"], "enemy_style": job["enemy_style"], "repeat": job["repeat"]}
            for job in jobs
        ],
    })
    pending = list(jobs)
    running: list[tuple[dict[str, Any], subprocess.Popen, Any]] = []
    failures = 0

    def start(job: dict[str, Any]) -> None:
        log_path = log_dir / "{0}.txt".format(job["id"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("w", encoding="utf-8")
        command = [
            sys.executable, "-m", "sc2bench_env.benchmark", "match", "run",
            "--config", str(config_dir / "{0}.json".format(job["id"])),
            "--record-dir", str(record_dir),
        ]
        process = subprocess.Popen(command, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
        running.append((job, process, log))
        print("start {0}  pid {1}".format(job["id"], process.pid), flush=True)

    try:
        while pending or running:
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
                row = {
                    "id": job["id"],
                    "opponent": job["opponent"],
                    "enemy_style": job["enemy_style"],
                    "repeat": job["repeat"],
                    "exit_code": code,
                    "log": str(log_dir / "{0}.txt".format(job["id"])),
                }
                _append_jsonl(results_path, row)
                if code != 0:
                    failures += 1
                print("done {0}  exit {1}  left {2}".format(job["id"], code, len(pending) + len(still_running)), flush=True)
            running = still_running
    except KeyboardInterrupt:
        print("stopping {0} running matches".format(len(running)), flush=True)
        for _job, process, log in running:
            _stop_process_tree(process)
            log.close()
        return 130
    print("grid finished  matches {0}  failures {1}  results {2}".format(len(jobs), failures, results_path), flush=True)
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
    jobs = build_jobs(_load_base(args.base), suite)
    stamp = datetime.now().strftime("%y%m%d_%H%M%S")
    grid_dir = args.grid_dir or (args.record_dir / "grids" / stamp)
    print(
        "suite {0}  matches {1}  parallel {2}".format(suite.to_dict()["suite_id"], len(jobs), args.parallel),
        flush=True,
    )
    if args.dry_run:
        _write_json(grid_dir / "plan.json", {
            "matches": len(jobs),
            "jobs": [job["id"] for job in jobs],
        })
        print("plan {0}".format(grid_dir / "plan.json"), flush=True)
        return 0
    return run_grid(jobs, parallel=args.parallel, record_dir=args.record_dir.resolve(), grid_dir=grid_dir.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
