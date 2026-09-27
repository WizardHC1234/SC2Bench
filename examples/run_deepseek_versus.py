"""DeepSeek 同模型异种族对打：TvP、TvZ、PvZ，每张图 20 局。

    python examples/run_deepseek_versus.py --dry-run
    python examples/run_deepseek_versus.py --max-parallel 2 --quiet

每局有两个模型。并发 2 局就是 4 路请求。人机那张表用
benchmarks/kimi_k25_ai.json，不要把互打放进 Suite。
同一格的对局写在同一个目录下。未指定 --record-dir 时，根目录是项目 records/batch。
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

if __package__:
    from . import agent_integration as agent_module
else:
    import agent_integration as agent_module

from sc2bench_env import EpisodeConfig
from sc2bench_env.paths import resolve_record_dir
from sc2bench_env.versus import VersusMatch, run_versus

MAPS = ("KairosJunctionLE", "AcropolisLE")
PAIRS = (("terran", "protoss"), ("terran", "zerg"), ("protoss", "zerg"))
REPETITIONS = 20
LAUNCH_ATTEMPTS = 3
_RACE = {"terran": "t", "protoss": "p", "zerg": "z"}
_MAP = {"KairosJunctionLE": "kairos", "AcropolisLE": "acropolis"}


def batch_root(record_dir: Path | None) -> Path:
    if record_dir is None:
        return resolve_record_dir(None) / "batch"
    return resolve_record_dir(record_dir)


def cell_id(race: str, enemy_race: str, map_name: str) -> str:
    return f"{_MAP[map_name]}_{_RACE[race]}_vs_{_RACE[enemy_race]}"


def jobs() -> list[dict]:
    planned = []
    index = 0
    for race, enemy_race in PAIRS:
        for map_name in MAPS:
            for repetition in range(1, REPETITIONS + 1):
                index += 1
                planned.append({
                    "index": index,
                    "cell": cell_id(race, enemy_race, map_name),
                    "race": race,
                    "enemy_race": enemy_race,
                    "map_name": map_name,
                    "repetition": repetition,
                    "attempt": 1,
                })
    return planned


def _run_job(job: dict) -> dict:
    config = EpisodeConfig(
        race=job["race"], enemy_race=job["enemy_race"], map_name=job["map_name"],
        game_time_limit_seconds=job["game_time_limit"], blocking_decisions=True,
    )
    try:
        home = agent_module.make_llm_call(
            api_key=agent_module.llm_api_key(), base_url=job["api_base_url"], model=job["model"],
            temperature=job["temperature"], timeout=job["api_timeout"], thinking=False,
        )
        away = agent_module.make_llm_call(
            api_key=agent_module.llm_api_key(), base_url=job["api_base_url"], model=job["model"],
            temperature=job["temperature"], timeout=job["api_timeout"], thinking=False,
        )
        options = dict(
            thinking_requested=False, verbose=False, skill_name="none",
            max_api_attempts=job["api_attempts"],
            api_retry_delay_seconds=job["api_retry_delay"],
            max_consecutive_rejections=job["max_rejections"],
        )
        match = VersusMatch(backend="sharpy", record_dir=Path(job["record_dir"]) / job["cell"])
        info = run_versus(
            match,
            agent_module.create_agent(home, **options),
            agent_module.create_agent(away, **options),
            config,
            max_decisions=job["max_decisions"], verbose=False,
        )
    except Exception as exc:
        return {**job, "status": "failed", "error": type(exc).__name__}
    players = info.get("players") or []
    return {
        **job,
        "status": "completed",
        "record_dir": info.get("record_dir"),
        "players": [
            {"player": row.get("player"), "result": row.get("result"), "end_reason": row.get("end_reason")}
            for row in players
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=agent_module.DEFAULT_MODEL)
    parser.add_argument("--api-base-url", default=agent_module.DEFAULT_API_BASE_URL)
    parser.add_argument("--temperature", type=agent_module.parse_temperature, default=agent_module.DEFAULT_TEMPERATURE)
    parser.add_argument("--api-timeout", type=agent_module.positive_float, default=180)
    parser.add_argument("--api-attempts", type=agent_module.positive_int, default=3)
    parser.add_argument("--api-retry-delay", type=agent_module.nonnegative_float, default=1)
    parser.add_argument("--max-rejections", type=agent_module.positive_int, default=3)
    parser.add_argument("--max-decisions", type=agent_module.positive_int, default=500)
    parser.add_argument("--game-time-limit", type=agent_module.positive_float, default=1800)
    parser.add_argument("--max-parallel", type=agent_module.positive_int, default=2)
    parser.add_argument("--record-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    planned = jobs()
    root = batch_root(args.record_dir)
    if args.dry_run:
        print(json.dumps({
            "episode_count": len(planned),
            "max_parallel": args.max_parallel,
            "model": args.model,
            "pairs": [list(pair) for pair in PAIRS],
            "maps": list(MAPS),
            "repetitions": REPETITIONS,
            "record_dir": str(root),
            "cells": {
                cell_id(race, enemy, map_name): str(root / cell_id(race, enemy, map_name))
                for race, enemy in PAIRS for map_name in MAPS
            },
        }, ensure_ascii=False, indent=2))
        return 0
    shared = {
        "model": args.model,
        "api_base_url": args.api_base_url,
        "temperature": args.temperature,
        "api_timeout": args.api_timeout,
        "api_attempts": args.api_attempts,
        "api_retry_delay": args.api_retry_delay,
        "max_rejections": args.max_rejections,
        "max_decisions": args.max_decisions,
        "game_time_limit": args.game_time_limit,
        "record_dir": str(root),
    }
    payload = [{**job, **shared} for job in planned]
    ctx = mp.get_context("spawn")
    finished = 0
    failed = 0
    pending = list(payload)
    try:
        while pending:
            wave = pending
            pending = []
            with ctx.Pool(args.max_parallel, maxtasksperchild=1) as pool:
                for row in pool.imap_unordered(_run_job, wave):
                    started = row.get("status") == "completed" and row.get("record_dir")
                    if started:
                        finished += 1
                    elif row.get("attempt", 1) < LAUNCH_ATTEMPTS:
                        pending.append({**row, "attempt": row.get("attempt", 1) + 1})
                        print(
                            f"{row['cell']} rep={row['repetition']} did not start "
                            f"(attempt {row.get('attempt', 1)}/{LAUNCH_ATTEMPTS}), retrying.",
                            flush=True,
                        )
                        continue
                    else:
                        finished += 1
                        failed += 1
                        print(
                            f"{row['cell']} rep={row['repetition']} still did not start "
                            f"after {LAUNCH_ATTEMPTS} attempts.",
                            flush=True,
                        )
                    print(
                        f"{finished}/{len(payload)} {row['race']} vs {row['enemy_race']} "
                        f"{row['map_name']} rep={row['repetition']} status={row.get('status')} "
                        f"players={row.get('players')}",
                        flush=True,
                    )
    except KeyboardInterrupt:
        print("Interrupted; completed match records remain.", flush=True)
        return 130
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
