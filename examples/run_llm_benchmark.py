"""示例：按 Suite 批量跑，每局新建一个 agents.llm_agent。

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
from sc2bench_env.benchmark import BenchmarkRunner, BenchmarkSuite

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
    runner = BenchmarkRunner(backend_factory=lambda: args.backend, record_dir=args.record_dir)
    if args.dry_run:
        print(json.dumps({
            "suite": suite.to_dict(), "suite_sha256": suite.sha256,
            "episode_count": len(suite.episode_plan()), "backend": args.backend,
            "agent_metadata": metadata, "record_dir": str(runner.record_dir),
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
    try:
        batch = runner.run(
            suite,
            lambda: BenchmarkLLMAgent(
                call_llm, thinking_requested=args.thinking,
                max_api_attempts=args.api_attempts,
                api_retry_delay_seconds=args.api_retry_delay,
                max_consecutive_rejections=args.max_rejections,
                verbose=not args.quiet,
            ),
            agent_metadata=metadata,
            max_parallel=args.max_parallel,
        )
    except KeyboardInterrupt:
        print("Interrupted; completed episode records remain.", flush=True)
        return 130
    return print_batch_result(batch)


if __name__ == "__main__":
    raise SystemExit(main())
