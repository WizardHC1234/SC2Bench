r"""底层 Environment 直接接入示例，不使用 BenchmarkRunner。

    python examples/llm_vs_ai.py --dry-run
    python examples/llm_vs_ai.py --opponent easy --enemy-style macro

用于理解 reset/get_context/step/close 和异步任务的反馈边界。
单局可以直接使用本例；需要 Runner 时看 agent_integration.py，批量看 run_llm_benchmark.py。
模型配置/API/解析与 LLMAgent 共用 agent_integration.py，不维护另一套密钥。
正常运行调用模型并启动SC2；--help/--dry-run 不调用API/游戏、不创建记录。
本例直接保存一局记录，没有 Runner 的运行索引和整批评估。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

if __package__:
    from . import agent_integration as agent_module
else:
    import agent_integration as agent_module

from sc2bench_env import Environment, EpisodeConfig, AgentInput, AgentStopped
from sc2bench_env.interface.observation_text import render_feedback_text


def run_episode(env: Environment, agent: agent_module.LLMAgent,
                config: EpisodeConfig, *, max_decisions: int = 500) -> dict[str, Any]:
    """显式交互流程；调用方负责 finally close，不伪造默认动作。"""
    if type(max_decisions) is not int or max_decisions < 1:
        raise ValueError("max_decisions must be a positive integer")
    # 1. 开始一局，得到初始观测；首次没有上一轮反馈。
    observation = env.reset(config)
    feedback = None
    print(f"record_dir={env.record_path}", flush=True)
    for index in range(max_decisions):
        # 2. 获取规则+文本Obs；Agent 自己组织上下文并调用模型。
        request = AgentInput(observation, feedback, env.get_context())
        try:
            turn = agent(request)
        except AgentStopped as stop:
            env.close(end_reason=stop.end_reason)
            return {"result": None, "end_reason": stop.end_reason}
        # API失败不计作非法动作、不偷偷提交wait；每次尝试独立记录。
        for failure in turn.call_failures:
            info = env.record_agent_call_failure(failure)
            if info["terminated"]:
                return info
        if turn.stop_after_call_failures:
            env.close(end_reason="agent_call_failed")
            return {"result": None, "end_reason": "agent_call_failed"}
        # 3. 环境接收动作数组与记录上下文，执行/等待后返回新状态。
        observation, feedback, terminated, info = env.step(
            turn.decision, agent_context=turn.agent_context)
        print(f"round={index + 1} game_seconds={observation.game.game_time_seconds:.1f} "
              f"terminated={terminated}", flush=True)
        if agent.verbose:
            print(render_feedback_text(feedback.to_dict()), flush=True)
        if terminated:
            return info
        # 拒绝原因进入下一轮Agent输入；合法持续任务无需重复提交。
    env.close(end_reason="decision_limit")
    return {"result": None, "end_reason": "decision_limit"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL") or agent_module.DEFAULT_MODEL)
    parser.add_argument("--api-base-url", default=os.environ.get("LLM_BASE_URL") or agent_module.DEFAULT_API_BASE_URL)
    parser.add_argument("--opponent", "--difficulty", type=agent_module.normalize_opponent,
                        choices=agent_module.BUILTIN_OPPONENTS, default="easy")
    parser.add_argument("--enemy-race", choices=agent_module.ENEMY_RACES, default="terran")
    parser.add_argument("--enemy-style", type=agent_module.normalize_enemy_style,
                        choices=agent_module.ENEMY_STYLES, default="random")
    parser.add_argument("--map", default="KairosJunctionLE")
    parser.add_argument("--game-time-limit", type=agent_module.positive_float, default=1800)
    parser.add_argument("--decision-interval", type=agent_module.positive_float, default=60)
    parser.add_argument("--max-decisions", type=agent_module.positive_int, default=500)
    parser.add_argument("--max-rejections", type=agent_module.positive_int, default=3)
    parser.add_argument("--temperature", type=agent_module.parse_temperature, default=agent_module.DEFAULT_TEMPERATURE)
    parser.add_argument("--api-timeout", type=agent_module.positive_float, default=180)
    parser.add_argument("--api-attempts", type=agent_module.positive_int, default=3)
    parser.add_argument("--api-retry-delay", type=agent_module.nonnegative_float, default=1)
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--decision-summary", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--non-blocking", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--record-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    config = EpisodeConfig(map_name=args.map, opponent=args.opponent, enemy_race=args.enemy_race,
                           enemy_style=args.enemy_style,
                           game_time_limit_seconds=args.game_time_limit,
                           decision_interval_seconds=args.decision_interval,
                           blocking_decisions=not args.non_blocking)
    if not args.model.strip() or not args.map.strip():
        parser.error("model and map must not be empty")
    if args.dry_run:
        from sc2bench_env.paths import resolve_record_dir
        print(json.dumps({"episode": config.to_dict(), "model": args.model,
                          "record_dir": str(resolve_record_dir(args.record_dir)),
                          "execution": "direct_environment"}, ensure_ascii=False, indent=2))
        return 0
    try:
        import json_repair
        client = agent_module.make_llm_call(
            api_key=agent_module.resolve_api_key(), base_url=args.api_base_url, model=args.model,
            temperature=args.temperature, timeout=args.api_timeout, thinking=args.thinking)
    except (ImportError, ValueError):
        parser.error("Check API configuration and install the LLM extra: pip install -e '.[llm]'")
    agent = agent_module.create_agent(
        client, decision_summary=args.decision_summary, thinking_requested=args.thinking,
        verbose=not args.quiet, max_api_attempts=args.api_attempts,
        api_retry_delay_seconds=args.api_retry_delay, max_consecutive_rejections=args.max_rejections)
    env = Environment("sharpy", record_dir=args.record_dir)
    try:
        info = run_episode(env, agent, config, max_decisions=args.max_decisions)
        print(f"result={info.get('result')} end_reason={info.get('end_reason')}", flush=True)
        return 0 if info.get("end_reason") in {"game_ended", "time_limit"} else 1
    except KeyboardInterrupt:
        env.close(end_reason="caller_interrupted")
        return 130
    except Exception as error:
        env.close(end_reason="agent_error")
        print(f"Stopped: {type(error).__name__}; inspect the episode record.", flush=True)
        return 1
    finally:
        # 4. 正常终局、失败或Ctrl+C都释放资源，不覆盖已有终局。
        env.close()


if __name__ == "__main__":
    raise SystemExit(main())
