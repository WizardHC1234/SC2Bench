"""Opt-in real SC2 smoke test. Run manually; never collected by pytest.

Reads SC2BENCH_LLM_BASE_URL and SC2BENCH_LLM_API_KEY. Each harness receives
a fresh agent and a short independent game. This does not measure win rate.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from functools import partial
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.experiment_agent import ExperimentAgent, HarnessConfig, HARNESSES
from agents.experiment_agent.agent import contains_tool_markup
from agents.experiment_agent.trace import attributed_replies, accepted_auxiliary_replies
from agents.experiment_agent.cli import metadata
from agents.experiment_agent.client import ChatClient
from agents.experiment_agent.config import PROVIDERS
from sc2bench_env.benchmark import BenchmarkRunner, BenchmarkSuite
from sc2bench_env.recording.reader import read_episode


def audit_batch(batch, harness):
    row = batch["episodes"][0]
    episode_dir = Path(row["record_directory"])
    episode = read_episode(episode_dir)
    calls = episode["metrics"]["model_calls"]
    roles = Counter(c["role"] for c in calls if c["status"] == "ok")
    tools = episode["metrics"]["tool_calls"]
    checks = {
        "reached_time_limit": row.get("end_reason") == "time_limit",
        "multiple_decisions": (row.get("decision_count") or 0) >= 2,
        "no_api_failures": all(c["status"] == "ok" for c in calls),
        "one_continuing_session": sum(m["role"] == "system" for m in episode["messages"]) == 1,
        "replay_saved": (episode_dir / "replay.SC2Replay").is_file(),
    }
    if harness == "plan_and_execute":
        checks["planner_and_executor"] = roles["planner"] >= 1 and roles["executor"] >= 2
        checks["replan_after_advance"] = any(c["role"] == "replanner" and c["game_time_seconds"] > 0 for c in calls)
    if harness == "reflection":
        checks["reflection_after_advance"] = any(c["role"] == "reflector" and c["game_time_seconds"] > 0 for c in calls)
        checks["no_initial_reflection"] = all(c["game_time_seconds"] > 0 for c in calls if c["role"] == "reflector")
        reviews = [r["content"] for r in accepted_auxiliary_replies(episode["messages"], calls) if r["role"] == "reflector"]
        markup_attempts = sum(contains_tool_markup(r["content"]) for r in attributed_replies(episode["messages"], calls) if r["role"] == "reflector")
        checks["reflection_without_encoded_tools"] = not any(contains_tool_markup(text) for text in reviews)
    if harness == "act_only":
        checks["no_auxiliary_modules"] = set(roles) <= {"actor"}
        checks["empty_actor_text"] = all(not (m.get("content") or "").strip()
                                          for m in episode["messages"] if m["role"] == "assistant")
    if harness == "plan_and_execute":
        current_plan = None
        focused = []
        for message in episode["messages"]:
            content = message.get("content") or ""
            if message["role"] == "assistant" and not message.get("tool_calls"):
                try:
                    value = json.loads(content)
                    if isinstance(value, dict) and isinstance(value.get("plan"), list) and value["plan"]:
                        current_plan = value["plan"]
                except (ValueError, TypeError):
                    pass
            if message["role"] == "user" and "Your current task is step 1: " in content:
                focused.append(current_plan is not None and content.endswith("Your current task is step 1: " + current_plan[0]))
        checks["executor_explicitly_assigned_first_step"] = bool(focused) and all(focused)
    report = {
        "harness": harness, "passed": all(checks.values()), "checks": checks,
        "status": row.get("status"), "end_reason": row.get("end_reason"),
        "game_time_seconds": row.get("game_time_seconds"), "decisions": row.get("decision_count"),
        "model_calls_by_role": dict(roles), "model_call_count": len(calls),
        "input_tokens": sum(c["input_tokens"] or 0 for c in calls),
        "output_tokens": sum(c["output_tokens"] or 0 for c in calls),
        "tool_statuses": dict(Counter(t.get("status", "unknown") for t in tools)),
        "record_directory": str(episode_dir),
    }
    if harness == "reflection":
        report["reflection_markup_attempts"] = markup_attempts
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harnesses", nargs="+", choices=HARNESSES, default=list(HARNESSES))
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", choices=PROVIDERS, default="deepseek")
    parser.add_argument("--map", dest="map_name", default="KairosJunctionLE")
    parser.add_argument("--game-seconds", type=float, default=120)
    parser.add_argument("--record-dir", type=Path)
    args = parser.parse_args(argv)
    args.temperature, args.api_timeout = 0.5, 120
    root = args.record_dir or ROOT / "records" / "experiments" / ("harness_smoke_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    client = ChatClient(model=args.model, provider=args.provider, temperature=args.temperature,
                        timeout=args.api_timeout, base_url=os.environ["SC2BENCH_LLM_BASE_URL"],
                        api_key=os.environ["SC2BENCH_LLM_API_KEY"])
    suite = BenchmarkSuite.from_dict({
        "schema_version": "1", "suite_id": "harness_real_game_smoke", "suite_version": "1",
        "purpose": "workflow_pilot", "repetitions": 1, "max_decisions": 500, "game_seeds": [0],
        "episode_defaults": {"race": "terran", "enemy_race": "terran", "map_name": args.map_name,
                             "opponent": "hard", "enemy_style": "rush", "blocking_decisions": True,
                             "realtime": False, "game_time_limit_seconds": args.game_seconds},
        "cases": [{"case_id": "tvt_hard_rush_smoke", "episode": {}}],
    })
    root.mkdir(parents=True, exist_ok=True)
    (root / "suite.json").write_text(json.dumps(suite.to_dict(), indent=2), encoding="utf-8")
    summary_path = root / "smoke_summary.json"
    reports = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else []
    print(f"SMOKE_ROOT {root}", flush=True)
    for harness in args.harnesses:
        config = HarnessConfig(harness=harness)
        print(f"START {harness} full_session=True max_calls=None max_output_tokens=None", flush=True)
        runner = BenchmarkRunner(backend_factory=partial(str, "sharpy"), record_dir=root / harness)
        batch = runner.run(suite, partial(ExperimentAgent, client, config, model=args.model),
                           agent_metadata=metadata(args, config))
        report = audit_batch(batch, harness)
        reports.append(report)
        summary_path.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
        print("SMOKE_RESULT " + json.dumps(report, ensure_ascii=False), flush=True)
    latest = {r["harness"]: r for r in reports}
    return 0 if all(r["passed"] for r in latest.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
