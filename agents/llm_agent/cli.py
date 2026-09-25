"""Command-line entry point for running one SC2Bench match."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sc2bench_env.benchmark import BenchmarkRunner
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.opponents import (
    BUILTIN_OPPONENTS,
    ENEMY_STYLES,
    normalize_enemy_style,
    normalize_opponent,
)
from sc2bench_env.interface.races import ENEMY_RACES, SUPPORTED_OWN_RACES

from .agent import LLMAgent
from .client import make_llm_call
from .config import (
    DEFAULT_TEMPERATURE,
    llm_api_key,
    llm_base_url,
    llm_model,
    parse_temperature,
    positive_float,
    positive_int,
)
from .skills import AVAILABLE_SKILLS, DEFAULT_SKILL, skill_race


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Minimal SC2Bench LLM Agent. One instance keeps one episode session.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--opponent", "--difficulty", type=normalize_opponent,
                        choices=BUILTIN_OPPONENTS, default="easy")
    parser.add_argument("--race", choices=SUPPORTED_OWN_RACES, default="terran",
                        help="own race against the built-in computer")
    parser.add_argument("--enemy-race", choices=ENEMY_RACES, default="terran")
    parser.add_argument("--enemy-style", type=normalize_enemy_style,
                        choices=ENEMY_STYLES, default="random")
    parser.add_argument("--map", default="KairosJunctionLE")
    parser.add_argument("--game-time-limit", type=positive_float, default=1800)
    parser.add_argument("--max-decisions", type=positive_int, default=500)
    parser.add_argument("--max-rejections", type=positive_int, default=3)
    parser.add_argument("--api-timeout", type=positive_float, default=120)
    parser.add_argument("--api-attempts", type=positive_int, default=3)
    parser.add_argument("--api-retry-delay", type=positive_float, default=1)
    parser.add_argument("--temperature", type=parse_temperature, default=DEFAULT_TEMPERATURE)
    parser.add_argument(
        "--thinking", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--skill", choices=(*AVAILABLE_SKILLS, "none"), default=DEFAULT_SKILL,
        help="whole-match strategy skill; use 'none' for the no-skill baseline",
    )
    parser.add_argument("--realtime", action="store_true",
                        help="run 1x while the model thinks; advance fast-forwards")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--record-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.skill != "none" and skill_race(args.skill) != args.race:
        parser.error(
            f"{args.skill} is a {skill_race(args.skill)} strategy, not {args.race}"
        )
    config = EpisodeConfig(
        race=args.race, enemy_race=args.enemy_race, map_name=args.map,
        enemy_style=args.enemy_style, opponent=args.opponent,
        blocking_decisions=True, game_time_limit_seconds=args.game_time_limit,
        realtime=args.realtime,
    )
    if args.dry_run:
        from sc2bench_env.paths import resolve_record_dir
        print(json.dumps({
            "episode": config.to_dict(),
            "model": llm_model(),
            "base_url": llm_base_url(),
            "skill": args.skill,
            "max_decisions": args.max_decisions,
            "record_dir": str(resolve_record_dir(args.record_dir)),
        }, ensure_ascii=False, indent=2))
        return 0
    call_llm = make_llm_call(
        api_key=llm_api_key(),
        base_url=llm_base_url(),
        model=llm_model(),
        timeout=args.api_timeout,
        temperature=args.temperature,
        thinking=args.thinking,
    )
    runner = BenchmarkRunner(record_dir=args.record_dir)
    batch = runner.run(
        [config],
        lambda: LLMAgent(
            call_llm, max_api_attempts=args.api_attempts,
            api_retry_delay_seconds=args.api_retry_delay,
            max_consecutive_rejections=args.max_rejections,
            thinking_requested=args.thinking, verbose=not args.quiet,
            skill_name=args.skill,
        ),
        max_decisions=args.max_decisions,
    )
    print(json.dumps(batch["aggregate"], ensure_ascii=False), flush=True)
    return 0 if all(row["status"] == "completed" for row in batch["episodes"]) else 1
