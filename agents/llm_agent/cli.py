"""Command-line entry point for running one SC2Bench match."""
from __future__ import annotations


import argparse
import json
from pathlib import Path
from typing import Any

from sc2bench_env import Environment
from sc2bench_env.interface.agent import AgentInput, AgentStopped, AgentTurn
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.opponents import (
    BUILTIN_OPPONENTS,
    ENEMY_STYLES,
    normalize_enemy_style,
    normalize_opponent,
)
from sc2bench_env.interface.races import ENEMY_RACES, SUPPORTED_OWN_RACES
from sc2bench_env.runtime.tool_turn import ToolTurnError

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


def run_episode(
    env: Environment, agent: LLMAgent, config: EpisodeConfig,
    *, max_decisions: int = 500,
) -> dict[str, Any]:
    """Run one Agent session directly against one Environment episode."""
    observation = env.reset(config)
    feedback = None
    print(f"record_dir={env.record_path}", flush=True)
    for _ in range(max_decisions):
        tool_turn = env.begin_tool_turn()
        try:
            turn = agent(AgentInput(
                observation, feedback, tuple(env.tool_specs()), tool_turn.call,
            ))
        except AgentStopped as stop:
            tool_turn.abort()
            env.close(end_reason=stop.end_reason)
            return {"result": None, "end_reason": stop.end_reason}
        if not isinstance(turn, AgentTurn):
            tool_turn.abort()
            env.record_protocol_error("agent_protocol_error")
            env.close(end_reason="agent_protocol_error")
            return {"result": None, "end_reason": "agent_protocol_error"}
        for failure in turn.call_failures:
            info = env.record_agent_call_failure(failure)
            if info["terminated"]:
                tool_turn.abort()
                return info
        if turn.stop_after_call_failures:
            env.record_agent_calls(turn.agent_context)
            tool_turn.abort()
            env.close(end_reason="agent_call_failed")
            return {"result": None, "end_reason": "agent_call_failed"}
        try:
            decision = tool_turn.finish()
        except ToolTurnError as error:
            env.record_protocol_error(error.code, agent_context=turn.agent_context)
            env.close(end_reason="agent_protocol_error")
            return {"result": None, "end_reason": "agent_protocol_error"}
        observation, feedback, terminated, info = env.step(
            decision, agent_context=turn.agent_context,
        )
        if terminated:
            return info
    env.close(end_reason="decision_limit")
    return {"result": None, "end_reason": "decision_limit"}


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
            "execution": "direct_environment",
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
    agent = LLMAgent(
            call_llm, max_api_attempts=args.api_attempts,
            api_retry_delay_seconds=args.api_retry_delay,
            max_consecutive_rejections=args.max_rejections,
            thinking_requested=args.thinking, verbose=not args.quiet,
            skill_name=args.skill,
    )
    env = Environment("sharpy", record_dir=args.record_dir)
    try:
        info = run_episode(env, agent, config, max_decisions=args.max_decisions)
        print(
            f"result={info.get('result')} end_reason={info.get('end_reason')}",
            flush=True,
        )
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
