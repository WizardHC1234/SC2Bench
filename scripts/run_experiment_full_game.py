"""Run one complete research-agent episode with the agreed 30-minute game limit."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.experiment_agent import ExperimentAgent, HarnessConfig, HARNESSES
from agents.experiment_agent.cli import metadata
from agents.experiment_agent.client import ChatClient
from agents.experiment_agent.config import PROVIDERS
from sc2bench_env import Environment
from sc2bench_env.benchmark import Evaluator
from sc2bench_env.interface.agent import AgentInput, AgentStopped
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.observations import agent_observation_view


def make_episode_config(map_name):
    return EpisodeConfig(race="terran", enemy_race="terran", map_name=map_name,
                         opponent="hard", enemy_style="rush", seed=0,
                         game_time_limit_seconds=1800.0, blocking_decisions=True, realtime=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=HARNESSES, default="react")
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", choices=PROVIDERS, default="deepseek")
    parser.add_argument("--thinking", action="store_true")
    parser.add_argument("--map", dest="map_name", default="KairosJunctionLE")
    parser.add_argument("--record-dir", type=Path)
    parser.add_argument("--check-worker-counters", action="store_true", help="Compare delivered worker inventory with SC2's completed-worker counter")
    args = parser.parse_args(argv)
    args.temperature, args.api_timeout = 0.5, 120
    if args.thinking and args.provider == "deepseek" and args.harness == "act_only":
        parser.error("DeepSeek thinking does not support Act-only's required tool choice")
    config = HarnessConfig(harness=args.harness, native_thinking=args.thinking)
    episode = make_episode_config(args.map_name)
    client = ChatClient(model=args.model, provider=args.provider, temperature=args.temperature,
                        timeout=args.api_timeout, base_url=os.environ["SC2BENCH_LLM_BASE_URL"],
                        api_key=os.environ["SC2BENCH_LLM_API_KEY"], native_thinking=config.native_thinking)
    root = args.record_dir or ROOT / "records" / "experiments" / ("full_game_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"status": "running", "purpose": "workflow_pilot", "episode_config": episode.to_dict(),
                "agent_metadata": metadata(args, config), "max_decisions": None,
                "automatic_episode_reruns": False}
    manifest_path = root / "run.json"
    worker_checks = {"decisions_checked": 0, "mismatches": [], "hidden_worker_decisions": 0, "examples": []}
    def save_manifest():
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    save_manifest()
    print("FULL_GAME_ROOT " + str(root), flush=True)
    env = Environment("sharpy", record_dir=root)
    try:
        observation = env.reset(episode)
        manifest["record_directory"] = str(env.record_path)
        save_manifest()
        agent = ExperimentAgent(client, config, model=args.model)
        feedback = None
        while not observation.terminated:
            if args.check_worker_counters:
                snapshot = env.backend.snapshot()
                expected = snapshot.info.get("supply_workers")
                worker_name = {"terran": "scv", "protoss": "probe", "zerg": "drone"}[episode.race]
                visible = len(snapshot.info.get("ready_unit_tags", {}).get(worker_name, []))
                delivered = observation.economy.worker_count
                worker_checks["decisions_checked"] += 1
                sample = {"seconds": observation.game.game_time_seconds, "native_completed_workers": expected,
                          "known_worker_tags": visible, "delivered_workers": delivered,
                          "gas_workers": observation.economy.workers_on_vespene}
                if delivered != expected:
                    worker_checks["mismatches"].append(sample)
                if expected is not None and expected > visible:
                    worker_checks["hidden_worker_decisions"] += 1
                    if len(worker_checks["examples"]) < 8:
                        worker_checks["examples"].append(sample)
            turn = env.begin_tool_turn()
            response = agent(AgentInput(agent_observation_view(observation), feedback,
                                        tuple(env.tool_specs()), turn.call))
            ended = False
            for failure in response.call_failures:
                if env.record_agent_call_failure(failure)["terminated"]:
                    ended = True
                    break
            if ended:
                turn.abort()
                break
            if response.stop_after_call_failures:
                env.record_agent_calls(response.agent_context)
                turn.abort()
                env.close(end_reason="agent_call_failed")
                break
            observation, feedback, terminated, _ = env.step(turn.finish(), agent_context=response.agent_context)
            if terminated:
                break
    except AgentStopped as stop:
        env.record_protocol_error(stop.end_reason, agent_context=stop.agent_context)
        env.close(end_reason=stop.end_reason)
    except KeyboardInterrupt:
        env.close(end_reason="caller_interrupted")
    except Exception as error:
        manifest["error_type"] = type(error).__name__
        env.close(end_reason="agent_error")
    finally:
        env.close()
        if env.record_path is not None:
            manifest["record_directory"] = str(env.record_path)
            manifest["result"] = Evaluator.evaluate_episode(env.record_path)
        result = manifest.get("result", {})
        completed = result.get("status") == "completed" and result.get("end_reason") in {"game_ended", "time_limit"}
        manifest["status"] = "completed" if completed else "unfinished"
        if args.check_worker_counters:
            manifest["worker_counter_validation"] = {**worker_checks,
                "passed": worker_checks["decisions_checked"] > 0 and not worker_checks["mismatches"]
                          and worker_checks["hidden_worker_decisions"] > 0}
        save_manifest()
    print("FULL_GAME_RESULT " + json.dumps(manifest.get("result"), ensure_ascii=False), flush=True)
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
