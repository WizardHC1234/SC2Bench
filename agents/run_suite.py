"""按 Suite 用无技能的 agents.llm_agent 打人机。

模型从 agents/config.json 的 llm_agents_pool 读取，用法和 Commander 一样，用 --model-key 选择。
先把 agents/config.example.json 复制为 agents/config.json，再填入自己的地址和密钥。

    python agents/run_suite.py --dry-run
    python agents/run_suite.py --model-key kimi-k2.5 --suite benchmarks/kimi_k25_ai.json --record-dir records/batch_kimi --max-parallel 5 --quiet
    python agents/run_suite.py --model-key deepseek-v4-flash --suite benchmarks/deepseek_flash_ai.json --max-parallel 5 --quiet
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agents.llm_agent import LLMAgent, make_llm_call
from agents.llm_agent.config import positive_float, positive_int
from sc2bench_env.benchmark import BenchmarkRunner, BenchmarkSuite, Evaluator
from sc2bench_env.paths import resolve_record_dir

DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.json"
DEFAULT_SUITE = _PROJECT_ROOT / "benchmarks" / "kimi_k25_ai.json"
LAUNCH_ATTEMPTS = 3
SKILL = "none"


def nonnegative_float(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return number


def load_model(config_path: Path, model_key: str) -> dict:
    data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    pool = data.get("llm_agents_pool")
    if not isinstance(pool, dict) or not pool:
        raise ValueError("config.json needs llm_agents_pool")
    row = pool.get(model_key)
    if not isinstance(row, dict):
        known = ", ".join(sorted(pool))
        raise ValueError(f"Unknown model-key {model_key!r}. Available: {known}")
    for field in ("api_url", "api_key", "model_name"):
        if not isinstance(row.get(field), str) or not row[field].strip():
            raise ValueError(f"{model_key} is missing {field}")
    return row


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
    return sum(1 for child in directory.iterdir() if child.is_dir() and episode_status(child) == "completed")


def game_started(row: dict) -> bool:
    if row.get("status") != "completed" and row.get("decision_count") in (None, 0):
        return False
    return row.get("record_directory") is not None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--model-key", default="kimi-k2.5")
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--record-dir", type=Path, default=None)
    parser.add_argument("--max-parallel", type=positive_int, default=1)
    parser.add_argument("--api-timeout", type=positive_float, default=180)
    parser.add_argument("--api-attempts", type=positive_int, default=3)
    parser.add_argument("--api-retry-delay", type=nonnegative_float, default=1)
    parser.add_argument("--max-rejections", type=positive_int, default=3)
    parser.add_argument("--backend", choices=("sharpy", "fake"), default="sharpy")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        model = load_model(args.config, args.model_key)
        suite = BenchmarkSuite.load(args.suite)
    except (OSError, ValueError, TypeError) as error:
        parser.error(str(error))
    thinking = bool(model.get("is_reasoning"))
    temperature = model.get("temperature", 0.5)
    if not isinstance(temperature, (int, float)):
        parser.error("temperature in config.json must be a number")
    root = (
        resolve_record_dir(args.record_dir)
        if args.record_dir is not None
        else resolve_record_dir(None) / f"batch_{args.model_key}"
    )
    if args.dry_run:
        print(json.dumps({
            "suite": str(args.suite),
            "suite_id": suite.to_dict()["suite_id"],
            "episode_count": len(suite.episode_plan()),
            "model_key": args.model_key,
            "model_name": model["model_name"],
            "api_url": model["api_url"],
            "temperature": temperature,
            "thinking": thinking,
            "skill": SKILL,
            "record_dir": str(root),
            "cells": {
                case["case_id"]: str(root / case["case_id"])
                for case in suite.to_dict()["cases"]
            },
        }, ensure_ascii=False, indent=2))
        return 0
    try:
        call_llm = make_llm_call(
            api_key=model["api_key"],
            base_url=model["api_url"],
            model=model["model_name"],
            timeout=args.api_timeout,
            temperature=float(temperature),
            thinking=thinking,
        )
    except ValueError as error:
        parser.error(str(error))

    def agent_factory():
        return LLMAgent(
            call_llm,
            thinking_requested=thinking,
            max_api_attempts=args.api_attempts,
            api_retry_delay_seconds=args.api_retry_delay,
            max_consecutive_rejections=args.max_rejections,
            verbose=not args.quiet,
            skill_name=SKILL,
        )

    metadata = {
        "name": "llm_agent",
        "kind": "llm",
        "implementation": "agents.llm_agent.LLMAgent",
        "model_requested": model["model_name"],
        "settings": {"skill": SKILL, "thinking": thinking, "temperature": temperature},
    }
    print(f"Calling {args.model_key} with skill={SKILL}.", flush=True)
    episodes = []
    data = suite.to_dict()
    try:
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
                    record_dir=cell_dir,
                ).run(one, agent_factory, agent_metadata=metadata, max_parallel=args.max_parallel)
                fresh = [row for row in batch["episodes"] if game_started(row)]
                started.extend(fresh)
                missed = need - len(fresh)
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
    print(f"Aggregate: {Evaluator.summarize(episodes)}", flush=True)
    print(f"Termination counts: {Evaluator.termination_counts(episodes)}", flush=True)
    return 0 if episodes and all(row["status"] == "completed" for row in episodes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
