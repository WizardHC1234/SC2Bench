r"""LLM 批量对战：在单局接入跑通后使用，不是平台内置策略。

在项目根目录运行：
    python -m examples.run_llm_benchmark --repetitions 1
    python examples/run_llm_benchmark.py --dry-run

模型/API/回复解析复用 agent_integration.py，配置网关或密钥只需修改那一处。
Suite 决定对局配置和次数；Runner 每局创建新 Agent，逐局记录并汇总。
--dry-run 只展示有效配置，不启动游戏、不调用API、不创建记录。
正常运行默认启动SC2并调用模型；--backend fake 仍然调用模型。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

if __package__:
    from . import agent_integration as llm
else:
    import agent_integration as llm

DEFAULT_SUITE = llm.DEFAULT_SUITE
BUILTIN_OPPONENTS = llm.BUILTIN_OPPONENTS
positive_int = llm.positive_int
positive_float = llm.positive_float
nonnegative_float = llm.nonnegative_float
temperature = llm.parse_temperature
print_batch_result = llm.print_batch_result

# Keep one API/parser implementation for single and batch examples.
DEFAULT_API_BASE_URL = llm.DEFAULT_API_BASE_URL
DEFAULT_MODEL = llm.DEFAULT_MODEL
DEFAULT_TEMPERATURE = llm.DEFAULT_TEMPERATURE
make_llm_call = llm.make_llm_call
resolve_api_key = llm.resolve_api_key
from sc2bench_env.benchmark import BenchmarkRunner, BenchmarkSuite


# 单局与批量使用同一个有状态 LLM Agent，不另维护规则策略。
if __package__:
    from .agent_integration import LLMAgent as BenchmarkLLMAgent, InvalidDecisionLimit
else:
    from agent_integration import LLMAgent as BenchmarkLLMAgent, InvalidDecisionLimit


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--repetitions", type=positive_int, default=None)
    parser.add_argument("--max-decisions", type=positive_int, default=None)
    parser.add_argument("--opponent", "--difficulty", type=llm.normalize_opponent,
                        choices=BUILTIN_OPPONENTS)
    parser.add_argument("--enemy-style", type=llm.normalize_enemy_style, choices=llm.ENEMY_STYLES,
                        default=None, help="override builtin AI style for every selected Suite case")
    parser.add_argument("--backend", choices=("sharpy", "fake"), default="sharpy",
                        help="fake replaces the game, NOT the LLM; model calls still occur")
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL") or DEFAULT_MODEL)
    parser.add_argument("--api-base-url", default=os.environ.get("LLM_BASE_URL") or DEFAULT_API_BASE_URL)
    parser.add_argument("--temperature", type=temperature, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--decision-summary", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--api-timeout", type=positive_float, default=120)
    parser.add_argument("--api-attempts", type=positive_int, default=3)
    parser.add_argument("--api-retry-delay", type=nonnegative_float, default=1)
    parser.add_argument("--max-rejections", type=positive_int, default=3)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--record-dir", type=Path, default=None)
    parser.add_argument("--results-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="effective Suite/settings preview, no API/game/records")
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

    source = Path(__file__).resolve()
    helper = source.with_name("agent_integration.py")
    metadata = {
        "name": "plain_llm_baseline", "kind": "llm",
        "implementation": "examples.agent_integration.LLMAgent",
        "model_requested": args.model, "model_service_revision": None,
        "settings": {
            "temperature": args.temperature, "thinking": args.thinking,
            "decision_summary": args.decision_summary, "api_timeout": args.api_timeout,
            "max_api_attempts": args.api_attempts, "api_retry_delay_seconds": args.api_retry_delay,
            "max_consecutive_rejections": args.max_rejections,
            "prompt_source": "Environment.get_context",
            "parser": "examples.agent_integration.parse_model_reply",
        },
        "source_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (source, helper)},
    }
    runner = BenchmarkRunner(backend_factory=lambda: args.backend,
                             record_dir=args.record_dir, results_dir=args.results_dir)
    if args.dry_run:
        print(json.dumps({
            "suite": suite.to_dict(), "suite_sha256": suite.sha256,
            "episode_count": len(suite.episode_plan()), "backend": args.backend,
            "agent_metadata": metadata, "record_dir": str(runner.record_dir),
            "results_dir": str(runner.results_dir),
        }, ensure_ascii=False, indent=2))
        return 0
    try:
        import json_repair
        call_llm = make_llm_call(
            api_key=resolve_api_key(), base_url=args.api_base_url, model=args.model,
            timeout=args.api_timeout, temperature=args.temperature, thinking=args.thinking,
        )
    except (ImportError, ValueError):
        parser.error("Check API configuration and install the LLM extra: python -m pip install -e '.[llm]'")
    print("Calling the configured LLM API; each episode has a fresh Agent.", flush=True)
    try:
        batch = runner.run(
            suite,
            lambda: BenchmarkLLMAgent(
                call_llm, thinking_requested=args.thinking,
                decision_summary=args.decision_summary, max_api_attempts=args.api_attempts,
                api_retry_delay_seconds=args.api_retry_delay,
                max_consecutive_rejections=args.max_rejections, verbose=not args.quiet,
            ),
            agent_metadata=metadata,
        )
    except KeyboardInterrupt:
        print("Interrupted; completed episodes remain in records and the batch index.", flush=True)
        return 130
    return print_batch_result(batch)


if __name__ == "__main__":
    raise SystemExit(main())
