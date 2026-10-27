"""Controlled native-game checks for six one-shot ability confirmations.

Uses normal platform tools and fresh games; no model, debug spawning or replay
player. The veryeasy opponent keeps this an interface check, not a benchmark.
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


def validate(race, root):
    from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
    _ensure_runtime_paths()
    from sc2bench_env.backends.sharpy.acts import _ObservedAbilityAct
    original_queue = _ObservedAbilityAct._queue_cast
    original_observe = _ObservedAbilityAct._observe_cast
    cast_confirmations = []
    def queue(act, caster, ability, target, **kwargs):
        result = original_queue(act, caster, ability, target, **kwargs)
        if act._pending_cast:
            act._validation_source = {"caster_type": caster.type_id.name, "ability": ability.name,
                                      "queued_at": act.ai.time}
        return result
    def observe(act):
        pending = act._pending_cast
        result = original_observe(act)
        if pending and result and act._done:
            cast_confirmations.append({**act._validation_source, "confirmed_at": act.ai.time})
        return result
    _ObservedAbilityAct._queue_cast = queue
    _ObservedAbilityAct._observe_cast = observe
    env = Environment("sharpy", record_dir=root / race)
    ability_ids = []
    rejected = []

    def step(calls=(), seconds=2):
        turn = env.begin_tool_turn()
        for name, args in calls:
            result = turn.call(ToolCall(name, args))
            if result.status != "staged":
                rejected.append({"tool": name, "status": result.status, "result": result.to_dict()})
        turn.call(ToolCall("advance", {"seconds": seconds}))
        return env.step(turn.finish())[0]

    def wait(predicate):
        nonlocal obs
        while not obs.terminated and obs.game.game_time_seconds < 480 and not predicate():
            obs = step(seconds=2)
        if not predicate():
            raise RuntimeError("Prerequisite or ability did not finish before test cutoff")

    try:
        obs = env.reset(EpisodeConfig(race=race, enemy_race="terran", opponent="veryeasy", enemy_style="macro",
                                      map_name="KairosJunctionLE", seed=0, game_time_limit_seconds=500))
        if race == "terran":
            obs = step([("build", {"target": "supply_depot"}), ("build", {"target": "barracks"})])
            wait(lambda: bool(obs.buildings.get("barracks")))
            obs = step([("morph_townhall", {"target": "townhall_0", "to": "orbital_command"})])
            wait(lambda: bool(obs.buildings.get("orbital_command")))
            calls = [("call_mule", {}), ("scan", {"target": obs.zones[0]}), ("supply_drop", {})]
        elif race == "protoss":
            calls = [("chrono_boost", {})]
        else:
            obs = step([("build", {"target": "spawning_pool"})])
            wait(lambda: bool(obs.buildings.get("spawning_pool")))
            obs = step([("train", {"target": "queen", "count": 1})])
            wait(lambda: bool(obs.units.get("queen")))
            calls = [("inject_larva", {}), ("spawn_creep_tumor", {})]
        before = set(env.task_manager.demands)
        obs = step(calls, seconds=0.5)
        ability_ids = sorted(set(env.task_manager.demands) - before)
        wait(lambda: all(not env.task_manager.demands[did].is_active for did in ability_ids))
        states = {did: {"action": env.task_manager.demands[did].action,
                        "state": env.task_manager.demands[did].state.value} for did in ability_ids}
        passed = len(ability_ids) == len(calls) and all(row["state"] == "completed" for row in states.values()) and not rejected
        result = {"race": race, "passed": passed, "ability_demands": states, "rejected": rejected,
                  "game_time_seconds": obs.game.game_time_seconds}
        if race == "zerg" and passed:
            first_cast_at = obs.game.game_time_seconds
            while obs.game.game_time_seconds - first_cast_at < 20:
                obs = step()
            energies = list(obs.abilities.get("queen_energies") or [])
            before = set(env.task_manager.demands)
            started_at = obs.game.game_time_seconds
            obs = step([("spawn_creep_tumor", {})], seconds=0.5)
            free_ids = sorted(set(env.task_manager.demands) - before)
            ability_ids.extend(free_ids)
            wait(lambda: all(not env.task_manager.demands[did].is_active for did in free_ids))
            elapsed = obs.game.game_time_seconds - started_at
            free_pass = (len(free_ids) == 1 and bool(energies) and max(energies) < 25
                         and env.task_manager.demands[free_ids[0]].state.value == "completed"
                         and any(row["ability"] == "BUILD_CREEPTUMOR_TUMOR" and row["queued_at"] >= started_at
                                 for row in cast_confirmations))
            result["free_tumor_spread"] = {"passed": free_pass, "queen_energy_before": energies,
                                           "completion_seconds": elapsed}
            result["passed"] = result["passed"] and free_pass
    except Exception as error:
        result = {"race": race, "passed": False, "error": str(error), "error_type": type(error).__name__,
                  "rejected": rejected}
    finally:
        if ability_ids:
            result["final_states"] = {did: {"action": env.task_manager.demands[did].action,
                "state": env.task_manager.demands[did].state.value,
                "waiting_for": env.task_manager.demands[did].waiting_for,
                "failure_reason": env.task_manager.demands[did].failure_reason} for did in ability_ids}
        env.close()
        _ObservedAbilityAct._queue_cast = original_queue
        _ObservedAbilityAct._observe_cast = original_observe
    result["cast_confirmations"] = cast_confirmations
    result["record_directory"] = str(env.record_path.relative_to(ROOT))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--races", nargs="+", choices=("terran", "protoss", "zerg"), default=["terran", "protoss", "zerg"])
    parser.add_argument("--record-dir", type=Path, default=ROOT / "records/experiments/implementation_audit/abilities")
    parser.add_argument("--diagnose", action="store_true")
    args = parser.parse_args()
    if args.diagnose:
        from sc2bench_env.backends.sharpy.backend import _ensure_runtime_paths
        _ensure_runtime_paths()
        from sc2bench_env.backends.sharpy.acts import _ObservedAbilityAct
        original = _ObservedAbilityAct._observe_cast
        def observe(act):
            pending = act._pending_cast
            result = original(act)
            if pending and act.ai.time > pending["time"] and not getattr(act, "_audit_seen", False):
                act._audit_seen = True
                print("CAST_EVIDENCE " + json.dumps({"ability": pending["ability"].name, "time": act.ai.time,
                    "target": list(pending["point"]), "result": result,
                    "new_entities": [{"type": u.type_id.name, "distance": u.distance_to(pending["point"])}
                                     for u in act.ai.all_own_units if u.tag not in pending["old_tags"]]}), flush=True)
            return result
        _ObservedAbilityAct._observe_cast = observe
    root = args.record_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    results = []
    for race in args.races:
        results.append(validate(race, root))
        (root / "validation.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf8")
        print("ABILITY_VALIDATION " + json.dumps(results[-1]), flush=True)
    return 0 if all(row["passed"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
