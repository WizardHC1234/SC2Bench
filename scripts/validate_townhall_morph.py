"""Controlled real-game regression: one morph request must survive a busy CC."""
import json
from pathlib import Path
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.tools import ToolCall
from scripts.audit_experiment_full_game import replay_facts
from sc2bench_env.recording.engine import iter_engine_samples


def audit_record(root, episode_dir):
    rows = list(iter_engine_samples(episode_dir / "evaluation.jsonl.gz"))
    steps = [r["event"] for r in rows if r["kind"] == "platform_event" and r["event"]["type"] == "step"]
    events = [e for step in steps for e in step["feedback"]["events"]]
    replay = replay_facts(episode_dir / "replay.SC2Replay")
    own = replay["own_player_id"]
    completions = [m["seconds"] for m in replay["morphs"] if m["owner"] == own and m["to"] == "OrbitalCommand"]
    confirmations = [e["game_time_seconds"] for e in events if e["type"] == "morph_townhall"]
    accepted = [e for e in events if e["type"] == "demand_accepted" and e.get("action") == "morph_townhall"]
    checks = {"one_request_only": len(accepted) == 1, "one_confirmed_start": len(confirmations) == 1,
              "orbital_really_completed": len(completions) == 1}
    if completions and confirmations:
        checks["confirmation_is_start_not_unverified_issue"] = 20 < completions[0] - confirmations[0] < 30
    report = {"passed": all(checks.values()), "checks": checks,
              "requested_at": accepted[0]["game_time_seconds"] if accepted else None,
              "confirmed_starts": confirmations, "replay_completions": completions,
              "record_directory": str(episode_dir)}
    (root / "validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    root = ROOT / "records" / "experiments" / ("morph_validation_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    env = Environment("sharpy", record_dir=root)
    def step(calls=(), seconds=2):
        turn = env.begin_tool_turn()
        for name, arguments in calls:
            result = turn.call(ToolCall(name, arguments))
            print("VALIDATION_TOOL " + json.dumps({"name": name, "result": result.to_dict()}), flush=True)
        turn.call(ToolCall("advance", {"seconds": seconds}))
        observation, feedback, terminated, _ = env.step(turn.finish())
        return observation, terminated
    try:
        obs = env.reset(EpisodeConfig(map_name="KairosJunctionLE", opponent="hard", enemy_style="rush",
                                      seed=0, game_time_limit_seconds=300))
        obs, ended = step([("build", {"target": "supply_depot"}), ("build", {"target": "barracks"})], 10)
        while not ended and not obs.buildings.get("barracks", 0):
            obs, ended = step(seconds=5)
        if ended:
            raise RuntimeError("Prerequisite setup did not finish")
        obs, ended = step([("train", {"target": "scv", "count": 4})], 1)
        obs, ended = step([("morph_townhall", {"target": "townhall_0", "to": "orbital_command"})], 1)
        while not ended and not obs.buildings.get("orbital_command", 0):
            obs, ended = step(seconds=1)
    finally:
        env.close()
    report = audit_record(root, env.record_path)
    print("VALIDATION_RESULT " + json.dumps(report), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
