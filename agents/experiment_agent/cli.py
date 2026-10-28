"""Batch entry point. Dry-run validates settings without creating a game/client."""
from __future__ import annotations

import argparse
from functools import partial
import hashlib
import json
import os
from pathlib import Path

from sc2bench_env.benchmark import BenchmarkRunner, BenchmarkSuite

from .agent import ExperimentAgent
from .client import ChatClient, plan_response_format
from .config import HARNESSES, PROVIDERS, HarnessConfig
from .prompts import fingerprints
from .suite import build_suite


def _positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def source_hash():
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def metadata(args, config):
    schedule = {}
    if config.harness == "reflection":
        schedule["reflection_trigger"] = "new_observation_after_game_time_advance"
    elif config.harness == "plan_and_execute":
        schedule["replan_trigger"] = "new_observation_after_game_time_advance"
        response_format = plan_response_format(args.provider)
        schedule["planner_response_format"] = response_format["type"]
        if "json_schema" in response_format:
            schedule["planner_response_schema"] = response_format["json_schema"]["schema"]
        schedule["executor_task_policy"] = "first_remaining_plan_step"
        schedule["execution_feedback"] = "tool_result_references_and_next_observation"
    elif config.harness == "act_only":
        schedule["tool_choice"] = "required"
    return {
        "name": "sc2bench_" + config.harness, "version": "3", "model": args.model,
        "implementation": "agents.experiment_agent.ExperimentAgent", "source_sha256": source_hash(),
        "settings": {**config.to_dict(), "provider": args.provider, "temperature": args.temperature,
                     "api_timeout_seconds": args.api_timeout, "conversation_mode": "full_session",
                     "native_thinking_requested": config.native_thinking, "cross_episode_memory": False,
                     "context_overflow_policy": "stop_without_truncation",
                     "advance_policy": "separate_reply",
                     "role_instruction_role": "system",
                     "uncertain_tool_error_policy": "record_and_stop_without_replay",
                     **schedule,
                     "prompt_fingerprints": fingerprints()},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=HARNESSES, default="react")
    parser.add_argument("--provider", choices=PROVIDERS, required=True)
    parser.add_argument("--model", required=True, help="Exact model ID exposed by your service")
    parser.add_argument("--suite", type=Path, help="Optional existing BenchmarkSuite JSON")
    parser.add_argument("--repetitions", type=_positive, default=None)
    parser.add_argument("--max-decisions", type=_positive, default=None)
    parser.add_argument("--max-calls-per-decision", type=_positive)
    parser.add_argument("--max-calls-per-episode", type=_positive)
    parser.add_argument("--max-output-tokens", type=_positive)
    parser.add_argument("--max-episode-output-tokens", type=_positive)
    parser.add_argument("--api-attempts", type=_positive, default=3)
    parser.add_argument("--retry-delay", type=float, default=1.0)
    parser.add_argument("--temperature", type=float, default=0.5)
    parser.add_argument("--thinking", action="store_true")
    parser.add_argument("--api-timeout", type=float, default=120)
    parser.add_argument("--max-parallel", type=_positive, default=1)
    parser.add_argument("--backend", choices=("fake", "sharpy"), default="sharpy")
    parser.add_argument("--record-dir", type=Path, default=Path("records/experiments"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = HarnessConfig(
            harness=args.harness, native_thinking=args.thinking, max_calls_per_decision=args.max_calls_per_decision,
            max_calls_per_episode=args.max_calls_per_episode, max_output_tokens=args.max_output_tokens,
            max_episode_output_tokens=args.max_episode_output_tokens,
            max_api_attempts=args.api_attempts, retry_delay_seconds=args.retry_delay,
        )
        if args.suite:
            suite = BenchmarkSuite.load(args.suite).with_overrides(
                repetitions=args.repetitions, max_decisions=args.max_decisions)
        else:
            suite = build_suite(repetitions=args.repetitions or 20, max_decisions=args.max_decisions)
        # All four methods use the agreed blocking, single-game protocol.
        for item in suite.episode_plan():
            episode = item["config"]
            if not episode.blocking_decisions or episode.realtime:
                raise ValueError("research agents require blocking, non-realtime episodes")
        # Validate transport settings even for a dry-run; no key is read there.
        if args.thinking and args.provider == "deepseek" and args.harness == "act_only":
            parser.error("DeepSeek thinking does not support Act-only's required tool choice")
        client = ChatClient(model=args.model, provider=args.provider, temperature=args.temperature, native_thinking=config.native_thinking,
                            timeout=args.api_timeout, base_url="http://localhost", api_key="dry-run")
    except (OSError, ValueError, TypeError) as error:
        parser.error(str(error))
    agent_metadata = metadata(args, config)
    if args.dry_run:
        print(json.dumps({"suite": suite.to_dict(), "suite_sha256": suite.sha256,
                          "episode_count": len(suite.episode_plan()), "backend": args.backend,
                          "agent_metadata": agent_metadata}, ensure_ascii=False, indent=2))
        return 0
    base_url = os.environ.get("SC2BENCH_LLM_BASE_URL", "")
    api_key = os.environ.get("SC2BENCH_LLM_API_KEY", "")
    try:
        client = ChatClient(model=args.model, provider=args.provider, base_url=base_url,
                            api_key=api_key, temperature=args.temperature, timeout=args.api_timeout, native_thinking=config.native_thinking)
    except ValueError:
        parser.error("Set SC2BENCH_LLM_BASE_URL and SC2BENCH_LLM_API_KEY for the selected provider")
    runner = BenchmarkRunner(backend_factory=partial(str, args.backend), record_dir=args.record_dir)
    result = runner.run(
        suite, partial(ExperimentAgent, client, config, model=args.model),
        agent_metadata=agent_metadata, max_parallel=args.max_parallel,
    )
    print(json.dumps({"status": result.get("status"), "batch_file": str(runner.batch_file)}, ensure_ascii=False))
    return 0 if result.get("status") == "completed" else 1
