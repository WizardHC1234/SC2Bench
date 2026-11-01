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
import hashlib
import math
from collections import Counter
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agents.llm_agent import LLMAgent, make_llm_call
from agents.llm_agent.agent import CONVERSATION_MODES
from agents.llm_agent.config import positive_float, positive_int
from sc2bench_env.benchmark import BenchmarkRunner, BenchmarkSuite, Evaluator
from sc2bench_env.paths import resolve_record_dir
from sc2bench_env.recording.trajectory import source_fingerprints

DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.json"
DEFAULT_SUITE = _PROJECT_ROOT / "benchmarks" / "kimi_k25_ai.json"
LAUNCH_ATTEMPTS = 3
SKILL = "none"


def nonnegative_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be finite and >= 0")
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


def episode_counts_for_quota(directory: Path) -> bool:
    """True only for a finished game that actually started.

    Launch failures, empty shells and interruptions do not fill the quota.
    Naturally finished short games remain valid outcomes.
    """
    try:
        payload = Evaluator.evaluate_episode(directory)
    except (OSError, ValueError, TypeError, KeyError):
        return False
    if payload.get("status") != "completed" or payload.get("end_reason") not in {"game_ended", "time_limit"}:
        return False
    decisions = payload.get("decision_count")
    game_s = payload.get("game_time_seconds")
    if type(decisions) is not int or decisions < 0:
        return False
    if not isinstance(game_s, (int, float)) or game_s <= 0:
        return False
    return True


def agent_fingerprint(implementation: str) -> str:
    import importlib.util
    module = implementation.rsplit(".", 1)[0]
    spec = importlib.util.find_spec(module)
    paths = set()
    if spec and spec.origin:
        paths.update(Path(spec.origin).parent.rglob("*.py"))
    paths.update((Path(__file__).parent / "llm_agent").rglob("*.py"))
    digest = hashlib.sha256()
    for path in sorted(paths):
        if path.name == "config.py":
            continue
        digest.update(path.relative_to(_PROJECT_ROOT).as_posix().encode("utf8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def ensure_run_identity(root: Path, suite: BenchmarkSuite, metadata: dict, *, service_url: str):
    """Fail closed on mixed experiments without modifying any prior records."""
    identity = {
        "suite_sha256": suite.sha256, "agent": metadata,
        "source": source_fingerprints(),
        "service_sha256": hashlib.sha256(service_url.encode("utf8")).hexdigest(),
    }
    root.mkdir(parents=True, exist_ok=True)
    path = root / "run_identity.json"
    if not path.exists() and next(root.rglob("episode.txt"), None) is not None:
        raise ValueError("Existing records have no experiment identity; use a new --record-dir.")
    try:
        with path.open("x", encoding="utf8") as out:
            json.dump(identity, out, ensure_ascii=False, sort_keys=True, indent=2)
    except FileExistsError:
        if json.loads(path.read_text(encoding="utf8")) != identity:
            raise ValueError("Record directory belongs to different experiment settings or source code; use a new --record-dir.")


def remaining_case_suite(data: dict, case: dict, directory: Path):
    """Retain the missing seed identities when rebuilding a partial suite."""
    planned = data["repetitions"]
    fields = {**data["episode_defaults"], **case["episode"]}
    seeds = data.get("game_seeds")
    completed = Counter()
    if directory.is_dir():
        for marker in directory.rglob("episode.txt"):
            if not episode_counts_for_quota(marker.parent):
                continue
            row = Evaluator.evaluate_episode(marker.parent)
            config = row.get("config") or {}
            if all(config.get(k) == v for k, v in fields.items()):
                completed[config.get("seed")] += 1
    if seeds is not None:
        missing = []
        for seed in seeds:
            if completed[seed]:
                completed[seed] -= 1
            else:
                missing.append(seed)
        need = len(missing)
    else:
        need = max(0, planned - sum(completed.values()))
    if not need:
        return None
    result = {**data, "cases": [case], "repetitions": need}
    if seeds is not None:
        result["game_seeds"] = missing
    return BenchmarkSuite.from_dict(result)


def completed_in_cell(directory: Path) -> int:
    """Count completed episode folders under a case cell.

    BenchmarkRunner writes ``batch_*/episodes/<episode>/``, so only looking at
    direct children would always report zero and never resume correctly.
    """
    if not directory.is_dir():
        return 0
    seen: set[Path] = set()
    count = 0
    for marker in directory.rglob("episode.txt"):
        parent = marker.parent.resolve()
        if parent in seen:
            continue
        seen.add(parent)
        if episode_counts_for_quota(parent):
            count += 1
    return count


def game_started(row: dict) -> bool:
    if row.get("status") != "completed" and row.get("decision_count") in (None, 0):
        return False
    return row.get("record_directory") is not None


def episode_completed(row: dict) -> bool:
    """Batch-index rows that represent a real finished game."""
    if row.get("status") != "completed" or row.get("record_directory") is None:
        return False
    decisions = row.get("decision_count")
    game_s = row.get("game_time_seconds")
    if type(decisions) is not int or decisions < 0:
        return False
    if not isinstance(game_s, (int, float)) or game_s <= 0:
        return False
    return row.get("end_reason") in {"game_ended", "time_limit"}


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
    parser.add_argument(
        "--conversation-mode", choices=CONVERSATION_MODES, default="full_session",
        help="retain the whole episode dialogue or start fresh at each decision",
    )
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
            "conversation_mode": args.conversation_mode,
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
            conversation_mode=args.conversation_mode,
        )

    metadata = {
        "name": "llm_agent",
        "version": "1",
        "model": model["model_name"],
        "kind": "llm",
        "implementation": "agents.llm_agent.LLMAgent",
        "model_requested": model["model_name"],
        "settings": {
            "skill": SKILL, "thinking": thinking, "temperature": temperature,
            "conversation_mode": args.conversation_mode,
            "api_timeout": args.api_timeout, "max_api_attempts": args.api_attempts,
            "api_retry_delay_seconds": args.api_retry_delay,
            "max_consecutive_rejections": args.max_rejections,
        },
    }
    metadata["agent_source_sha256"] = agent_fingerprint(metadata["implementation"])
    try:
        ensure_run_identity(root, suite, metadata, service_url=model["api_url"])
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"Calling {args.model_key} with skill={SKILL}.", flush=True)
    episodes = []
    data = suite.to_dict()
    try:
        for case in data["cases"]:
            planned = data["repetitions"]
            cell_dir = root / case["case_id"]
            remaining = remaining_case_suite(data, case, cell_dir)
            already = planned - (remaining.to_dict()["repetitions"] if remaining else 0)
            if already >= planned:
                print(f"{case['case_id']}: {already} completed, skip.", flush=True)
                continue
            shortfall = planned - already
            print(f"{case['case_id']}: {already} completed, scheduling {shortfall}.", flush=True)
            finished = []
            stagnant = 0
            previous = already
            # Only completed episodes fill the quota. Interrupted/failed games are
            # rescheduled in the same case instead of waiting for a later suite run.
            for attempt in range(1, LAUNCH_ATTEMPTS + planned):
                one = remaining_case_suite(data, case, cell_dir)
                if one is None:
                    break
                need = one.to_dict()["repetitions"]
                already = planned - need
                batch = BenchmarkRunner(
                    backend_factory=lambda: args.backend,
                    record_dir=cell_dir,
                ).run(one, agent_factory, agent_metadata=metadata, max_parallel=args.max_parallel)
                fresh_ok = [row for row in batch["episodes"] if episode_completed(row)]
                fresh_bad = [
                    row for row in batch["episodes"]
                    if game_started(row) and not episode_completed(row)
                ]
                finished.extend(fresh_ok)
                remaining = remaining_case_suite(data, case, cell_dir)
                already = planned - (remaining.to_dict()["repetitions"] if remaining else 0)
                if fresh_bad:
                    print(
                        f"{case['case_id']}: {len(fresh_bad)} game(s) started but did not "
                        f"complete; rescheduling ({attempt}).",
                        flush=True,
                    )
                missed = need - len(fresh_ok) - len(fresh_bad)
                if missed:
                    print(
                        f"{case['case_id']}: {missed} game(s) did not start "
                        f"(attempt {attempt}).",
                        flush=True,
                    )
                if already <= previous:
                    stagnant += 1
                else:
                    stagnant = 0
                previous = already
                if already >= planned:
                    break
                if stagnant >= LAUNCH_ATTEMPTS:
                    print(
                        f"{case['case_id']}: still short {planned - already} "
                        f"after {stagnant} rounds with no new completions.",
                        flush=True,
                    )
                    break
            episodes.extend(finished)
    except KeyboardInterrupt:
        print("Interrupted; completed episode records remain.", flush=True)
        return 130
    for index, row in enumerate(episodes, start=1):
        row["index"] = index
    print(f"Aggregate: {Evaluator.summarize(episodes)}", flush=True)
    print(f"Termination counts: {Evaluator.termination_counts(episodes)}", flush=True)
    complete = all(remaining_case_suite(data, c, root / c["case_id"]) is None for c in data["cases"])
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
