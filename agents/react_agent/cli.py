"""Run a ReAct episode with shared model settings and episode recording."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents.llm_agent.agent import CONVERSATION_MODES
from agents.llm_agent.cli import run_episode
from agents.llm_agent.client import make_llm_call
from agents.llm_agent.config import (
    DEFAULT_TEMPERATURE, llm_api_key, llm_base_url, llm_model,
    parse_temperature, positive_float, positive_int,
)
from agents.llm_agent.prompts import MAX_TOOL_ROUNDS
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.opponents import (
    BUILTIN_OPPONENTS, ENEMY_STYLES, normalize_enemy_style, normalize_opponent,
)
from sc2bench_env.interface.races import ENEMY_RACES, SUPPORTED_OWN_RACES
from sc2bench_env.paths import resolve_record_dir

from .agent import ReActAgent


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="SC2Bench ReAct agent; one session per game.")
    parser.add_argument("--opponent", "--difficulty", type=normalize_opponent,
                        choices=BUILTIN_OPPONENTS, default="easy")
    parser.add_argument("--race", choices=SUPPORTED_OWN_RACES, default="terran")
    parser.add_argument("--enemy-race", choices=ENEMY_RACES, default="terran")
    parser.add_argument("--enemy-style", type=normalize_enemy_style,
                        choices=ENEMY_STYLES, default="random")
    parser.add_argument("--map", default="KairosJunctionLE")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--game-time-limit", type=positive_float, default=1800)
    parser.add_argument("--max-decisions", type=positive_int, default=500)
    parser.add_argument("--max-tool-rounds", type=positive_int, default=MAX_TOOL_ROUNDS)
    parser.add_argument("--max-rejections", type=positive_int, default=3)
    parser.add_argument("--api-timeout", type=positive_float, default=120)
    parser.add_argument("--api-attempts", type=positive_int, default=3)
    parser.add_argument("--api-retry-delay", type=positive_float, default=1)
    parser.add_argument("--temperature", type=parse_temperature, default=None)
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--conversation-mode", choices=CONVERSATION_MODES, default="full_session")
    parser.add_argument("--model-key", default=None,
                        help="select an existing entry from agents/config.json")
    parser.add_argument("--config", type=Path,
                        default=Path(__file__).resolve().parents[1] / "config.json")
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--record-dir", type=Path, default=Path("records/react_agent"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    model = None
    if args.model_key:
        from agents.run_suite import load_model
        try:
            model = load_model(args.config, args.model_key)
        except (OSError, ValueError, TypeError) as error:
            parser.error(str(error))
    model_name = model["model_name"] if model else llm_model()
    base_url = model["api_url"] if model else llm_base_url()
    try:
        temperature = parse_temperature(str(
            args.temperature if args.temperature is not None else
            model.get("temperature", DEFAULT_TEMPERATURE) if model else DEFAULT_TEMPERATURE
        ))
    except (ValueError, argparse.ArgumentTypeError) as error:
        parser.error(str(error))
    thinking = (args.thinking if args.thinking is not None else
                bool(model.get("is_reasoning", False)) if model else False)
    config = EpisodeConfig(
        race=args.race, enemy_race=args.enemy_race, map_name=args.map,
        enemy_style=args.enemy_style, opponent=args.opponent, seed=args.seed,
        blocking_decisions=True, game_time_limit_seconds=args.game_time_limit,
        realtime=args.realtime,
    )
    if args.dry_run:
        print(json.dumps({
            "agent": "agents.react_agent.ReActAgent",
            "episode": config.to_dict(), "model": model_name, "base_url": base_url,
            "temperature": temperature, "thinking": thinking, "skill": "none",
            "conversation_mode": args.conversation_mode,
            "max_decisions": args.max_decisions, "max_tool_rounds": args.max_tool_rounds,
            "record_dir": str(resolve_record_dir(args.record_dir)),
        }, ensure_ascii=False, indent=2))
        return 0

    try:
        call_llm = make_llm_call(
            api_key=model["api_key"] if model else llm_api_key(),
            base_url=base_url, model=model_name, timeout=args.api_timeout,
            temperature=temperature, thinking=thinking,
        )
    except ValueError as error:
        parser.error(str(error))
    agent = ReActAgent(
        call_llm, max_api_attempts=args.api_attempts,
        api_retry_delay_seconds=args.api_retry_delay,
        max_consecutive_rejections=args.max_rejections,
        thinking_requested=thinking, verbose=not args.quiet, skill_name="none",
        conversation_mode=args.conversation_mode, max_tool_rounds=args.max_tool_rounds,
    )
    env = Environment("sharpy", record_dir=args.record_dir)
    try:
        info = run_episode(env, agent, config, max_decisions=args.max_decisions)
        print(f"result={info.get('result')} end_reason={info.get('end_reason')}", flush=True)
        return 0 if info.get("end_reason") in {"game_ended", "time_limit"} else 1
    except KeyboardInterrupt:
        env.close(end_reason="caller_interrupted")
        print("Interrupted; saved episode record remains available.", flush=True)
        return 130
    except Exception:
        env.close(end_reason="agent_error")
        print("Stopped; inspect the episode record.", flush=True)
        return 1
    finally:
        env.close()
