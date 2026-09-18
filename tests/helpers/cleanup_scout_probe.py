"""Opt-in real-engine/LLM cleanup probe; not a default example or benchmark.

Run explicitly: python -m tests.helpers.cleanup_scout_probe --model deepseek-v4-flash
  --output .test_artifacts/cleanup-scout-probe
Debug creates preconditions only. Normal fog, platform scouting and combat remain
enabled; hidden fixture coordinates never enter Agent messages.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from unittest.mock import patch


def line_distance(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = dx * dx + dy * dy
    t = max(0, min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length)) if length else 0
    return math.hypot(point[0] - start[0] - t * dx, point[1] - start[1] - t * dy)


def summarize_probe(evidence, episode_summary=None):
    """Separate Agent requests, engine discovery, retargeting and termination."""
    turns = evidence.get("turns", [])
    scouts = [t for t in turns if any(a.get("action") == "scout" for a in t["actions"])
              and any(r.get("action") == "scout" and r.get("result") == "accepted"
                      for r in t["feedback"].get("receipts", []))]
    hidden_zone = evidence.get("hidden_zone")
    seen = [t for t in turns if any(z["zone_id"] == hidden_zone and
            (any(z["visible_enemy_contents"].values()) or any(z["last_seen_enemy_contents"].values()))
            for z in t["zone_state"])]
    first_seen = seen[0]["index"] if seen else None
    retargets = [t["index"] for t in turns if first_seen is not None and t["index"] > first_seen
                and any(a.get("action") == "combat" and a.get("target") == hidden_zone
                        for a in t["actions"])]
    summary = episode_summary or {}
    return {"llm_decisions": len(turns),
            "first_accepted_scout_turn": scouts[0]["index"] if scouts else None,
            "used_route_all": any(a.get("action") == "scout" and a.get("route") == "all"
                                  for t in turns for a in t["actions"]),
            "scout_engine_discovery": bool(evidence.get("scout_discoveries")),
            "first_hidden_enemy_in_obs_turn": first_seen,
            "first_retarget_after_observed_discovery_turn": retargets[0] if retargets else None,
            "episode_status": summary.get("status"), "result": summary.get("result"),
            "end_reason": summary.get("end_reason"), "rejected_count": summary.get("rejected_count")}


def run_probe(*, model: str, output: Path, max_decisions: int = 20):
    from examples import agent_integration as llm
    from sc2bench_env import Environment
    from sc2bench_env.benchmark import AgentInput, AgentStopped
    from sc2bench_env.interface.config import EpisodeConfig
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    import sc2
    from sc2.bot_ai import BotAI
    from sc2.data import Race
    from sc2.ids.unit_typeid import UnitTypeId
    from sc2.player import Bot
    from sc2bench_env.backends.sharpy.acts import ActScoutRoute

    state = {"hidden_position": None, "hidden_zone": None, "hidden_tags": [],
             "original_tag": None, "original_destroyed": False, "fixture_error": None,
             "scout_discoveries": []}

    class CleanupOpponent(BotAI):
        def __init__(self):
            super().__init__()
            # Same distance path as Sharpy; BurnySC2's older default cdist path
            # references removed numpy.float on this pinned NumPy runtime.
            self.distance_calculation_method = 0

        async def on_start(self):
            try:
                original = self.townhalls.first
                state["original_tag"] = original.tag
                candidates = [p for p in self.expansion_locations_list
                              if p.distance_to(self.start_location) > 25
                              and p.distance_to(self.enemy_start_locations[0]) > 25]
                hidden = max(candidates, key=lambda p: line_distance(
                    p, self.start_location, self.enemy_start_locations[0]))
                state["hidden_position"] = hidden
                await self.client.debug_create_unit([
                    [UnitTypeId.COMMANDCENTER, 1, hidden, self.player_id],
                    [UnitTypeId.SCV, 2, hidden.towards(self.game_info.map_center, 6), self.player_id],
                    [UnitTypeId.MARINE, 24, self.enemy_start_locations[0].towards(self.game_info.map_center, 8), 1],
                    [UnitTypeId.SIEGETANK, 4, self.enemy_start_locations[0].towards(self.game_info.map_center, 12), 1],
                    [UnitTypeId.SUPPLYDEPOT, 4, self.enemy_start_locations[0].towards(self.game_info.map_center, 15), 1],
                ])
                # No enemy economy/rebuilds. Original CC is destroyed by actual
                # platform combat, not debug; the hidden CC prevents early victory.
                await self.client.debug_kill_unit(self.workers)
            except Exception as exc:
                state["fixture_error"] = type(exc).__name__
                raise

        async def on_step(self, iteration):
            originals = [u for u in self.structures if u.tag == state["original_tag"]]
            hidden = state["hidden_position"]
            state["hidden_tags"] = [u.tag for u in self.all_own_units
                                    if hidden is not None and u.distance_to(hidden) < 12]
            state["original_destroyed"] = not originals
            # Deliberately passive fixture, not a claimed VeryEasy AI trial.

    original_run_game = sc2.run_game
    original_scout_execute = ActScoutRoute.execute

    async def observe_scout(act):
        result = await original_scout_execute(act)
        scout = act.ai.workers.find_by_tag(act._scout_tag) if act._scout_tag else None
        hidden = state["hidden_position"]
        visible = [u for u in act.ai.all_enemy_units if u.tag in state["hidden_tags"]
                   and not u.is_snapshot and not u.is_memory]
        if scout is not None and hidden is not None and scout.distance_to(hidden) < 13 and visible:
            recorded = {name for row in state["scout_discoveries"] for name in row["visible_types"]}
            if {u.type_id.name for u in visible} - recorded:
                state["scout_discoveries"].append({"game_seconds": act.ai.time,
                    "scout_position": list(scout.position),
                    "visible_types": sorted({u.type_id.name for u in visible})})
        return result

    def controlled_game(game_map, players, **kwargs):
        result = original_run_game(game_map, [players[0], Bot(Race.Terran, CleanupOpponent())], **kwargs)
        return result[0] if isinstance(result, list) else result

    def wait(seconds=10):
        return {"action": "wait", "any_of": [{"condition": "interval", "seconds": seconds}]}

    def visible_enemies(observation, kind):
        counts = {}
        for zone in observation.zone_state:
            for name, count in zone["visible_enemy_contents"][kind].items():
                counts[name] = counts.get(name, 0) + count
        return counts

    output.mkdir(parents=True, exist_ok=True)
    evidence = {"scenario": "controlled_cleanup_hidden_expansion", "model": model,
                "skill": None, "thinking": False, "debug_setup": True,
                "hidden_location_disclosed_to_agent": False, "turns": []}
    env = Environment("sharpy")
    try:
        with patch.object(sc2, "run_game", controlled_game), patch.object(ActScoutRoute, "execute", observe_scout):
            obs = env.reset(EpisodeConfig(
                opponent="builtin_veryeasy", decision_interval_seconds=60,
                game_time_limit_seconds=900, blocking_decisions=True,
                extra={"test_scenario": evidence["scenario"],
                       "actual_opponent": "passive controlled Bot; not builtin AI",
                       "debug_preconditions": True}))
            evidence["record_directory"] = str(env.record_path)
            main = next(row["zone_id"] for row in obs.zone_state if row["zone_role"] == "enemy_main")
            # Permit debug-created units to appear and gather before binding.
            for _ in range(12):
                obs, _, done, _ = env.step([wait(5)])
                if obs.own_forces.free.get("marine", 0) >= 24 and obs.own_forces.free.get("siege_tank", 0) >= 4:
                    break
                if done:
                    raise RuntimeError("fixture ended during setup")
            obs, feedback, done, _ = env.step([
                {"action": "combat", "style": "attack", "target": main,
                 "units": {"marine": 24, "siege_tank": 4}}, wait()])
            if feedback.receipts[0].result != "accepted":
                raise RuntimeError("fixture army dispatch failed")
            for _ in range(35):
                row = next(row for row in obs.zone_state if row["zone_id"] == main)
                if (state["original_destroyed"] and row["vision_state"] == "visible"
                        and not any(row["visible_enemy_contents"].values())
                        and not visible_enemies(obs, "units") and not visible_enemies(obs, "buildings")):
                    break
                obs, feedback, done, _ = env.step([wait()])
                if done:
                    raise RuntimeError("fixture ended before cleanup handoff")
            else:
                raise RuntimeError("original enemy main not cleared")
            registry = env.backend._bridge.zone_registry
            # Private coordinates are only for verifier mapping, never prompt text.
            hidden_zone = min(obs.zones, key=lambda key: math.dist(
                registry.center_for(key), state["hidden_position"]))
            state["hidden_zone"] = hidden_zone
            hidden_row = next(row for row in obs.zone_state if row["zone_id"] == hidden_zone)
            if (hidden_row["vision_state"] != "fogged" or hidden_row["known_owner"] == "enemy"
                    or any(hidden_row["last_seen_enemy_contents"].values())):
                raise RuntimeError("hidden fixture was already exposed before model handoff")
            evidence["hidden_zone"] = hidden_zone
            evidence["handoff_observation"] = obs.to_dict()
            evidence["handoff_game_seconds"] = obs.game.game_time_seconds
            evidence["hidden_position"] = list(state["hidden_position"])
            evidence["hidden_alive_at_handoff"] = bool(state["hidden_tags"])
            if not evidence["hidden_alive_at_handoff"]:
                raise RuntimeError("no hidden fixture enemies remain")
            client = llm.make_llm_call(api_key=llm.resolve_api_key(), base_url=llm.DEFAULT_API_BASE_URL,
                                       model=model, thinking=False, timeout=120,
                                       temperature=llm.DEFAULT_TEMPERATURE)
            agent = llm.LLMAgent(client, verbose=False, decision_summary=True,
                                thinking_requested=False, skill_text="")
            for index in range(max_decisions):
                turn = agent(AgentInput(obs, feedback, env.get_context()))
                for failure in turn.call_failures:
                    env.record_agent_call_failure(failure)
                if turn.stop_after_call_failures:
                    evidence["stop_reason"] = "agent_call_failed"
                    break
                obs, feedback, done, info = env.step(turn.decision, agent_context=turn.agent_context)
                row = {"index": index + 1, "game_seconds": obs.game.game_time_seconds,
                       "actions": turn.decision, "feedback": feedback.to_dict(),
                       "scouting": obs.scouting, "enemy_units": visible_enemies(obs, "units"),
                       "enemy_buildings": visible_enemies(obs, "buildings"), "combat": obs.combat,
                       "zone_state": obs.zone_state, "terminated": done}
                evidence["turns"].append(row)
                print(json.dumps({key: row[key] for key in (
                    "index", "game_seconds", "actions", "scouting", "enemy_units", "enemy_buildings", "terminated")},
                    ensure_ascii=False), flush=True)
                if done:
                    evidence["stop_reason"] = info.get("end_reason") or env.backend.snapshot().end_reason
                    break
            else:
                evidence["stop_reason"] = "decision_budget"
            evidence["final_observation"] = obs.to_dict()
    except AgentStopped as exc:
        evidence["stop_reason"] = exc.end_reason
    except Exception as exc:
        evidence["stop_reason"] = "probe_error"
        evidence["error_type"] = type(exc).__name__
        raise
    finally:
        env.close()
        evidence["scout_discoveries"] = state["scout_discoveries"]
        evidence["fixture_error"] = state["fixture_error"]
        from sc2bench_env.recording.reader import read_episode
        episode_summary = read_episode(env.record_path)["summary"] if env.record_path else None
        evidence["assessment"] = summarize_probe(evidence, episode_summary)
        # Runtime evidence export, not a source-file edit.
        (output / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Probe evidence:", output / "evidence.json", flush=True)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-decisions", type=int, default=20)
    args = parser.parse_args()
    if args.max_decisions <= 0:
        parser.error("max-decisions must be positive")
    run_probe(model=args.model, output=args.output, max_decisions=args.max_decisions)


if __name__ == "__main__":
    main()
