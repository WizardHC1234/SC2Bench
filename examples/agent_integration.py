"""示例：BenchmarkRunner 管一局，决策用 agents.llm_agent。

    python examples/agent_integration.py --dry-run
    python examples/agent_integration.py --opponent easy

这个文件只演示接入。模型客户端、工具循环和记录都在 agents.llm_agent。
手写 reset/step 看 llm_vs_ai.py，批量看 run_llm_benchmark.py。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agents.llm_agent import (
    DEFAULT_API_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE,
    LLMAgent,
    create_agent,
    llm_api_key,
    make_llm_call,
)
from agents.llm_agent.config import positive_float, positive_int
from sc2bench_env.benchmark import BenchmarkRunner
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.opponents import (
    BUILTIN_OPPONENTS,
    ENEMY_STYLES,
    normalize_enemy_style,
    normalize_opponent,
)
from sc2bench_env.interface.races import ENEMY_RACES


def nonnegative_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def parse_temperature(value: str) -> float:
    try:
        number = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if not math.isfinite(number) or not 0 <= number <= 2:
        raise argparse.ArgumentTypeError("temperature must be between 0 and 2")
    return number


def print_batch_result(batch: dict) -> int:
    """Traversal completion is not proof that every episode completed."""
    for row in batch["episodes"]:
        print(
            f"{row['index']}. {row['config']['opponent']}: {row['status']}/"
            f"{row['outcome']} ({row['end_reason']}), "
            f"decisions={row['decision_count']}, rejected={row['rejected_count']}",
            flush=True,
        )
    print(f"Aggregate: {batch['aggregate']}", flush=True)
    print(f"Termination counts: {batch['termination_counts']}", flush=True)
    return 0 if all(row["status"] == "completed" for row in batch["episodes"]) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    parser.add_argument("--temperature", type=parse_temperature, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--non-blocking", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--opponent", "--difficulty", type=normalize_opponent,
                        choices=BUILTIN_OPPONENTS, default="easy")
    parser.add_argument("--enemy-race", choices=ENEMY_RACES, default="terran")
    parser.add_argument("--enemy-style", type=normalize_enemy_style,
                        choices=ENEMY_STYLES, default="random")
    parser.add_argument("--map", default="KairosJunctionLE")
    parser.add_argument("--game-time-limit", type=positive_float, default=1800)
    parser.add_argument("--max-decisions", type=positive_int, default=500)
    parser.add_argument("--max-rejections", type=positive_int, default=3)
    parser.add_argument("--api-timeout", type=positive_float, default=120)
    parser.add_argument("--api-attempts", type=positive_int, default=3)
    parser.add_argument("--api-retry-delay", type=nonnegative_float, default=1)
    parser.add_argument("--record-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.map.strip() or not args.model.strip():
        parser.error("map and model must not be empty")
    config = EpisodeConfig(
        race="terran", enemy_race=args.enemy_race, map_name=args.map,
        enemy_style=args.enemy_style, opponent=args.opponent,
        blocking_decisions=not args.non_blocking,
        game_time_limit_seconds=args.game_time_limit,
    )
    if args.dry_run:
        from sc2bench_env.paths import resolve_record_dir
        print(json.dumps({
            "episode": config.to_dict(), "model": args.model,
            "temperature": args.temperature, "thinking": args.thinking,
            "max_decisions": args.max_decisions,
            "max_rejections": args.max_rejections, "api_timeout": args.api_timeout,
            "api_attempts": args.api_attempts, "api_retry_delay": args.api_retry_delay,
            "record_dir": str(resolve_record_dir(args.record_dir)),
        }, ensure_ascii=False, indent=2))
        return 0
    try:
        call_llm = make_llm_call(
            api_key=llm_api_key(), base_url=args.api_base_url, model=args.model,
            timeout=args.api_timeout, temperature=args.temperature, thinking=args.thinking,
        )
    except ValueError:
        parser.error("Check API configuration before starting a game")

    def create_episode_agent():
        return LLMAgent(
            call_llm, thinking_requested=args.thinking, verbose=not args.quiet,
            max_api_attempts=args.api_attempts,
            api_retry_delay_seconds=args.api_retry_delay,
            max_consecutive_rejections=args.max_rejections,
        )

    runner = BenchmarkRunner(backend_factory=lambda: "sharpy", record_dir=args.record_dir)
    metadata = {
        "name": "llm_agent", "kind": "llm",
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
    print("Runner is starting one episode with agents.llm_agent.", flush=True)
    try:
        batch = runner.run(
            [config], agent_factory=create_episode_agent,
            max_decisions=args.max_decisions, agent_metadata=metadata)
    except KeyboardInterrupt:
        print("Interrupted; saved episode records remain available.", flush=True)
        return 130
    return print_batch_result(batch)


if __name__ == "__main__":
    raise SystemExit(main())
