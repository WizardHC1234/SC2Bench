"""示例：两个彼此独立的人族 Agent 对战。各自的 advance 到点才询问那一边。

    python examples/llm_vs_llm.py --dry-run
    python examples/llm_vs_llm.py --model DeepSeek-V4-Flash --opponent-model other-model

两边可以是不同的模型、地址和 skill。run_versus 也接受任意两个可调用对象，不要求同一个类。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

if __package__:
    from . import agent_integration as agent_module
else:
    import agent_integration as agent_module

from agents.llm_agent.config import env_setting
from sc2bench_env import EpisodeConfig, VersusMatch, run_versus
from sc2bench_env.interface.races import SUPPORTED_OWN_RACES


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=agent_module.DEFAULT_MODEL)
    parser.add_argument("--opponent-model", default=None)
    parser.add_argument("--api-base-url", default=agent_module.DEFAULT_API_BASE_URL)
    parser.add_argument("--opponent-api-base-url", default=None)
    parser.add_argument("--race", choices=SUPPORTED_OWN_RACES, default="terran")
    parser.add_argument("--enemy-race", choices=SUPPORTED_OWN_RACES, default="terran")
    parser.add_argument("--map", default="KairosJunctionLE")
    parser.add_argument("--game-time-limit", type=agent_module.positive_float, default=1800)
    parser.add_argument("--max-decisions", type=agent_module.positive_int, default=500)
    parser.add_argument("--max-rejections", type=agent_module.positive_int, default=3)
    parser.add_argument("--skill", default="tank")
    parser.add_argument("--opponent-skill", default="tank")
    parser.add_argument("--temperature", type=agent_module.parse_temperature,
                        default=agent_module.DEFAULT_TEMPERATURE)
    parser.add_argument("--opponent-temperature", type=agent_module.parse_temperature, default=None)
    parser.add_argument("--api-timeout", type=agent_module.positive_float, default=180)
    parser.add_argument("--api-attempts", type=agent_module.positive_int, default=3)
    parser.add_argument("--api-retry-delay", type=agent_module.nonnegative_float, default=1)
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--record-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    config = EpisodeConfig(
        race=args.race, enemy_race=args.enemy_race, map_name=args.map,
        game_time_limit_seconds=args.game_time_limit, blocking_decisions=True,
        realtime=args.realtime,
    )
    if not args.model.strip() or not args.map.strip():
        parser.error("model and map must not be empty")
    opponent_model = args.opponent_model or args.model
    opponent_url = args.opponent_api_base_url or args.api_base_url
    opponent_temperature = args.temperature if args.opponent_temperature is None else args.opponent_temperature
    if args.dry_run:
        from sc2bench_env.paths import resolve_record_dir
        print(json.dumps({
            "episode": config.to_dict(),
            "opponent_mode": "agent",
            "agents": [
                {"model": args.model, "skill": args.skill},
                {"model": opponent_model, "skill": args.opponent_skill},
            ],
            "record_dir": str(resolve_record_dir(args.record_dir)),
            "execution": "versus",
        }, ensure_ascii=False, indent=2))
        return 0
    try:
        home = agent_module.make_llm_call(
            api_key=agent_module.llm_api_key(), base_url=args.api_base_url, model=args.model,
            temperature=args.temperature, timeout=args.api_timeout, thinking=args.thinking)
        opponent_key = env_setting("LLM_OPPONENT_API_KEY", agent_module.llm_api_key())
        away = agent_module.make_llm_call(
            api_key=opponent_key, base_url=opponent_url, model=opponent_model,
            temperature=opponent_temperature, timeout=args.api_timeout, thinking=args.thinking)
    except ValueError:
        parser.error("Check API configuration before starting a game")
    options = dict(
        thinking_requested=args.thinking, verbose=not args.quiet,
        max_api_attempts=args.api_attempts, api_retry_delay_seconds=args.api_retry_delay,
        max_consecutive_rejections=args.max_rejections,
    )
    agent = agent_module.create_agent(home, skill_name=args.skill, **options)
    opponent = agent_module.create_agent(away, skill_name=args.opponent_skill, **options)
    match = VersusMatch(backend="sharpy", record_dir=args.record_dir)
    try:
        info = run_versus(
            match, agent, opponent, config,
            max_decisions=args.max_decisions, verbose=not args.quiet,
        )
    except KeyboardInterrupt:
        match.close(end_reason="caller_interrupted")
        return 130
    players = info.get("players") or []
    for row in players:
        print(
            f"player={row['player']} result={row['result']} end_reason={row['end_reason']}",
            flush=True,
        )
    reasons = {row.get("end_reason") for row in players}
    return 0 if reasons & {"game_ended", "time_limit"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
