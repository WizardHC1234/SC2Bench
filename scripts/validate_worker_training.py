"""Reproduce the hidden-gas-worker training target in a complete native game.

No model, debug spawning, turn cap or cancel/reissue workaround.
After the eight-worker request completes, continue until terminal or 30 minutes.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.tools import ToolCall
from sc2bench_env.benchmark import Evaluator
from scripts.audit_experiment_full_game import replay_facts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.record_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    env = Environment("sharpy", record_dir=root)
    samples, names, request_id, completed_at = [], [], None, None
    error = None
    def step(calls=(), seconds=15):
        turn = env.begin_tool_turn()
        for name, arguments in calls:
            result = turn.call(ToolCall(name, arguments))
            if result.status != "staged":
                raise RuntimeError(f"Setup command {name} returned {result.status}")
            names.append(name)
        turn.call(ToolCall("advance", {"seconds": seconds}))
        obs = env.step(turn.finish())[0]
        snap = env.backend.snapshot()
        samples.append({"seconds": obs.game.game_time_seconds, "delivered": obs.economy.worker_count,
                        "native_completed": snap.info.get("supply_workers"),
                        "known_tags": len(snap.info.get("ready_unit_tags", {}).get("scv", []))})
        return obs
    try:
        obs = env.reset(EpisodeConfig(race="terran", enemy_race="terran", opponent="hard", enemy_style="rush",
                                      map_name="KairosJunctionLE", seed=0, game_time_limit_seconds=1800.0,
                                      blocking_decisions=True, realtime=False))
        obs = step([("train", {"target": "scv", "count": 8})])
        while not obs.terminated and obs.game.game_time_seconds < 38:
            obs = step()
        if not obs.terminated:
            obs = step([("build", {"target": "supply_depot"})])
        while not obs.terminated and obs.economy.worker_count < 16:
            obs = step()
        if not obs.terminated:
            obs = step([("build", {"target": "refinery"}), ("build", {"target": "refinery"}),
                        ("build", {"target": "supply_depot"})])
        while not obs.terminated and obs.buildings.get("refinery", 0) < 2:
            obs = step()
        if not obs.terminated:
            before = set(env.task_manager.demands)
            obs = step([("train", {"target": "scv", "count": 8})])
            request_id = (set(env.task_manager.demands) - before).pop()
        while not obs.terminated:
            if request_id and env.task_manager.demands[request_id].state.value == "completed" and completed_at is None:
                completed_at = obs.game.game_time_seconds
            obs = step()
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        env.close()
    result = Evaluator.evaluate_episode(env.record_path)
    replay = replay_facts(env.record_path / "replay.SC2Replay")
    native_peak = max(s["workers"] for s in replay["stats_by_player"][str(replay["own_player_id"])] if s["workers"] is not None)
    checks = {"episode_completed": result["status"] == "completed" and result["end_reason"] in {"game_ended", "time_limit"},
              "thirty_minute_limit_respected": result["game_time_seconds"] <= 1801,
              "single_eight_worker_request_completed": request_id is not None and completed_at is not None,
              "no_cancel_or_reissue_workaround": "cancel" not in names and names.count("train") == 2,
              "native_replay_reached_24_workers": native_peak == 24,
              "all_delivered_totals_match_native_counter": all(s["delivered"] == s["native_completed"] for s in samples),
              "hidden_workers_exercised": any(s["native_completed"] > s["known_tags"] for s in samples),
              "no_script_error": error is None}
    output = {"passed": all(checks.values()), "checks": checks, "request_id": request_id,
              "request_completed_at": completed_at, "native_peak_workers": native_peak,
              "sample_count": len(samples), "hidden_samples": [s for s in samples if s["native_completed"] > s["known_tags"]][:8],
              "counter_mismatches": [s for s in samples if s["delivered"] != s["native_completed"]],
              "outcome": result["result"], "game_time_seconds": result["game_time_seconds"],
              "record_directory": str(env.record_path.relative_to(ROOT)), "error": error}
    (root / "validation.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(output, ensure_ascii=False), flush=True)
    return 0 if output["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
