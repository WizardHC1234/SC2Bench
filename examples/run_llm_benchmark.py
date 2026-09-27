"""示例：按 Suite 批量跑，每局新建一个 agents.llm_agent。

同一格的对局写在同一个目录下。未指定 --record-dir 时，根目录是项目 records/batch。

    python examples/run_llm_benchmark.py --dry-run --repetitions 1
    python examples/run_llm_benchmark.py --repetitions 1
    python examples/run_llm_benchmark.py --repetitions 1 --max-parallel 2
"""
from __future__ import annotations


import argparse
import json
import os
from pathlib import Path

if __package__:
    from . import agent_integration as llm
else:
    import agent_integration as llm

from agents.llm_agent import LLMAgent as BenchmarkLLMAgent
from sc2bench_env.benchmark import BenchmarkRunner, BenchmarkSuite, Evaluator
from sc2bench_env.paths import resolve_record_dir

DEFAULT_SUITE = Path(__file__).resolve().parents[1] / "benchmarks" / "terran_pilot.json"
DEFAULT_API_BASE_URL = llm.DEFAULT_API_BASE_URL
DEFAULT_MODEL = llm.DEFAULT_MODEL
DEFAULT_TEMPERATURE = llm.DEFAULT_TEMPERATURE
BUILTIN_OPPONENTS = llm.BUILTIN_OPPONENTS
positive_int = llm.positive_int
positive_float = llm.positive_float
nonnegative_float = llm.nonnegative_float
temperature = llm.parse_temperature
print_batch_result = llm.print_batch_result
make_llm_call = llm.make_llm_call


LAUNCH_ATTEMPTS = 3


def episode_status(directory: Path) -> str | None:
    episode = directory / "episode.txt"
    if not episode.is_file():
        return None
    text = episode.read_text(encoding="utf-8")
    marker = text.rfind("\nResult\n")
    if marker < 0:
        return None
    try:
        payload = json.loads(text[marker + len("\nResult\n"):])
    except json.JSONDecodeError:
        return None
    status = payload.get("status")
    return status if isinstance(status, str) else None


def completed_in_cell(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(
        1 for child in directory.iterdir()
        if child.is_dir() and episode_status(child) == "completed"
    )


def game_started(row: dict) -> bool:
    """A launch that never reached a decision does not fill the cell."""
    if row.get("status") != "completed" and row.get("decision_count") in (None, 0):
        return False
    return row.get("record_directory") is not None


def batch_root(record_dir: Path | None) -> Path:
    if record_dir is None:
        return resolve_record_dir(None) / "batch"
    return resolve_record_dir(record_dir)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--repetitions", type=positive_int, default=None)
    parser.add_argument("--max-decisions", type=positive_int, default=None)
    parser.add_argument("--opponent", "--difficulty", type=llm.normalize_opponent,
                        choices=BUILTIN_OPPONENTS)
    parser.add_argument("--enemy-style", type=llm.normalize_enemy_style,
                        choices=llm.ENEMY_STYLES, default=None)
    parser.add_argument("--backend", choices=("sharpy", "fake"), default="sharpy")
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL") or DEFAULT_MODEL)
    parser.add_argument("--api-base-url", default=os.environ.get("LLM_BASE_URL") or DEFAULT_API_BASE_URL)
    parser.add_argument("--temperature", type=temperature, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--api-timeout", type=positive_float, default=120)
    parser.add_argument("--api-attempts", type=positive_int, default=3)
    parser.add_argument("--api-retry-delay", type=nonnegative_float, default=1)
    parser.add_argument("--max-rejections", type=positive_int, default=3)
    parser.add_argument("--max-parallel", type=positive_int, default=1)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--record-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        suite = BenchmarkSuite.load(args.suite).with_overrides(
            repetitions=args.repetitions, max_decisions=args.max_decisions,
            opponents=(args.opponent,) if args.opponent else None,
            enemy_style=args.enemy_style,
        )
    except (OSError, ValueError, TypeError):
        parser.error("Check the Suite path, case selection and supported fields")
    if not args.model.strip():
        parser.error("model must not be empty")
    metadata = {
        "name": "plain_llm_baseline", "kind": "llm",
        "implementation": "agents.llm_agent.LLMAgent",
        "model_requested": args.model,
        "settings": {
            "temperature": args.temperature, "thinking": args.thinking,
            "api_timeout": args.api_timeout,
            "max_api_attempts": args.api_attempts,
            "api_retry_delay_seconds": args.api_retry_delay,
            "max_consecutive_rejections": args.max_rejections,
        },
    }
    root = batch_root(args.record_dir)
    cells = {
        case["case_id"]: str(root / case["case_id"])
        for case in suite.to_dict()["cases"]
    }
    if args.dry_run:
        print(json.dumps({
            "suite": suite.to_dict(), "suite_sha256": suite.sha256,
            "episode_count": len(suite.episode_plan()), "backend": args.backend,
            "agent_metadata": metadata, "record_dir": str(root), "cells": cells,
        }, ensure_ascii=False, indent=2))
        return 0
    try:
        call_llm = make_llm_call(
            api_key=llm.llm_api_key(), base_url=args.api_base_url, model=args.model,
            timeout=args.api_timeout, temperature=args.temperature, thinking=args.thinking,
        )
    except ValueError:
        parser.error("Check API configuration before starting a game")
    print("Calling the configured LLM API; each episode has a fresh Agent.", flush=True)

    def agent_factory():
        return BenchmarkLLMAgent(
            call_llm, thinking_requested=args.thinking,
            max_api_attempts=args.api_attempts,
            api_retry_delay_seconds=args.api_retry_delay,
            max_consecutive_rejections=args.max_rejections,
            verbose=not args.quiet,
        )

    episodes = []
    try:
        data = suite.to_dict()
        for case in data["cases"]:
            planned = data["repetitions"]
            cell_dir = root / case["case_id"]
            already = completed_in_cell(cell_dir)
            if already >= planned:
                print(f"{case['case_id']}: {already} completed, skip.", flush=True)
                continue
            shortfall = planned - already
            print(f"{case['case_id']}: {already} completed, scheduling {shortfall}.", flush=True)
            started = []
            for attempt in range(1, LAUNCH_ATTEMPTS + 1):
                need = shortfall - len(started)
                if need < 1:
                    break
                one = BenchmarkSuite.from_dict({**data, "cases": [case], "repetitions": need})
                batch = BenchmarkRunner(
                    backend_factory=lambda: args.backend,
                    record_dir=root / case["case_id"],
                ).run(
                    one, agent_factory, agent_metadata=metadata,
                    max_parallel=args.max_parallel,
                )
                fresh = [row for row in batch["episodes"] if game_started(row)]
                missed = need - len(fresh)
                started.extend(fresh)
                if missed:
                    print(
                        f"{case['case_id']}: {missed} game(s) did not start "
                        f"(attempt {attempt}/{LAUNCH_ATTEMPTS}).",
                        flush=True,
                    )
            if len(started) < shortfall:
                print(
                    f"{case['case_id']}: still short {shortfall - len(started)} "
                    f"after {LAUNCH_ATTEMPTS} launch attempts.",
                    flush=True,
                )
            episodes.extend(started)
    except KeyboardInterrupt:
        print("Interrupted; completed episode records remain.", flush=True)
        return 130
    for index, row in enumerate(episodes, start=1):
        row["index"] = index
    return print_batch_result({
        "episodes": episodes,
        "aggregate": Evaluator.summarize(episodes),
        "termination_counts": Evaluator.termination_counts(episodes),
    })


if __name__ == "__main__":
    raise SystemExit(main())
