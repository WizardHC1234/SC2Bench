"""示例：自己写 reset / step / close，不经过 BenchmarkRunner。

    python examples/llm_vs_ai.py --dry-run
    python examples/llm_vs_ai.py --opponent easy --enemy-style macro

看 run_episode()。模型仍是 agents.llm_agent，这里不解析回复。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

if __package__:
    from . import agent_integration as agent_module
else:
    import agent_integration as agent_module

from sc2bench_env import AgentInput, AgentStopped, Environment, EpisodeConfig
from sc2bench_env.interface.observation_text import render_feedback_text


def run_episode(env: Environment, agent: agent_module.LLMAgent,
                config: EpisodeConfig, *, max_decisions: int = 500) -> dict[str, Any]:
    """显式交互流程；调用方负责 finally close。"""
    if type(max_decisions) is not int or max_decisions < 1:
        raise ValueError("max_decisions must be a positive integer")
    observation = env.reset(config)
    feedback = None
    print(f"record_dir={env.record_path}", flush=True)
    for index in range(max_decisions):
        request = AgentInput(
            observation, feedback, env.get_context(),
            tool_schemas=tuple(env.tool_schemas()),
            call_tool=env.call_tool,
        )
        try:
            turn = agent(request)
        except AgentStopped as stop:
            env.close(end_reason=stop.end_reason)
            return {"result": None, "end_reason": stop.end_reason}
        for failure in turn.call_failures:
            info = env.record_agent_call_failure(failure)
            if info["terminated"]:
                return info
        if turn.stop_after_call_failures:
            env.close(end_reason="agent_call_failed")
            return {"result": None, "end_reason": "agent_call_failed"}
        if turn.decision is None:
            env.close(end_reason="agent_call_failed")
            return {"result": None, "end_reason": "agent_call_failed"}
        observation, feedback, terminated, info = env.step(
            turn.decision, agent_context=turn.agent_context)
        print(f"round={index + 1} game_seconds={observation.game.game_time_seconds:.1f} "
              f"terminated={terminated}", flush=True)
        if agent.verbose:
            print(render_feedback_text(feedback.to_dict()), flush=True)
        if terminated:
            return info
    env.close(end_reason="decision_limit")
    return {"result": None, "end_reason": "decision_limit"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=agent_module.DEFAULT_MODEL)
    parser.add_argument("--api-base-url", default=agent_module.DEFAULT_API_BASE_URL)
    parser.add_argument("--opponent", "--difficulty", type=agent_module.normalize_opponent,
                        choices=agent_module.BUILTIN_OPPONENTS, default="easy")
    parser.add_argument("--enemy-race", choices=agent_module.ENEMY_RACES, default="terran")
    parser.add_argument("--enemy-style", type=agent_module.normalize_enemy_style,
                        choices=agent_module.ENEMY_STYLES, default="random")
    parser.add_argument("--map", default="KairosJunctionLE")
    parser.add_argument("--game-time-limit", type=agent_module.positive_float, default=1800)
    parser.add_argument("--max-decisions", type=agent_module.positive_int, default=500)
    parser.add_argument("--max-rejections", type=agent_module.positive_int, default=3)
    parser.add_argument("--temperature", type=agent_module.parse_temperature,
                        default=agent_module.DEFAULT_TEMPERATURE)
    parser.add_argument("--api-timeout", type=agent_module.positive_float, default=180)
    parser.add_argument("--api-attempts", type=agent_module.positive_int, default=3)
    parser.add_argument("--api-retry-delay", type=agent_module.nonnegative_float, default=1)
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--non-blocking", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--record-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    config = EpisodeConfig(
        map_name=args.map, opponent=args.opponent, enemy_race=args.enemy_race,
        enemy_style=args.enemy_style, game_time_limit_seconds=args.game_time_limit,
        blocking_decisions=not args.non_blocking)
    if not args.model.strip() or not args.map.strip():
        parser.error("model and map must not be empty")
    if args.dry_run:
        from sc2bench_env.paths import resolve_record_dir
        print(json.dumps({
            "episode": config.to_dict(), "model": args.model,
            "record_dir": str(resolve_record_dir(args.record_dir)),
            "execution": "direct_environment",
        }, ensure_ascii=False, indent=2))
        return 0
    try:
        client = agent_module.make_llm_call(
            api_key=agent_module.llm_api_key(), base_url=args.api_base_url, model=args.model,
            temperature=args.temperature, timeout=args.api_timeout, thinking=args.thinking)
    except ValueError:
        parser.error("Check API configuration before starting a game")
    agent = agent_module.create_agent(
        client, thinking_requested=args.thinking, verbose=not args.quiet,
        max_api_attempts=args.api_attempts, api_retry_delay_seconds=args.api_retry_delay,
        max_consecutive_rejections=args.max_rejections)
    env = Environment("sharpy", record_dir=args.record_dir)
    try:
        info = run_episode(env, agent, config, max_decisions=args.max_decisions)
        print(f"result={info.get('result')} end_reason={info.get('end_reason')}", flush=True)
        return 0 if info.get("end_reason") in {"game_ended", "time_limit"} else 1
    except KeyboardInterrupt:
        env.close(end_reason="caller_interrupted")
        return 130
    except Exception:
        env.close(end_reason="agent_error")
        print("Stopped; inspect the episode record.", flush=True)
        return 1
    finally:
        env.close()


if __name__ == "__main__":
    raise SystemExit(main())
