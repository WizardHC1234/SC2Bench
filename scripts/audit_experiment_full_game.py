"""Read-only audit of session, model calls, detailed logs and replay tracker events."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mpyq import MPQArchive
from s2protocol.versions import build, latest
from sc2.data import Result
from sc2bench_env.recording.reader import read_episode
from sc2bench_env.recording.engine import iter_engine_samples
from agents.experiment_agent.protocol import validate_plan
from agents.experiment_agent.agent import contains_tool_markup
from agents.experiment_agent.trace import accepted_auxiliary_replies
from sc2bench_env.interface.tools import READ_TOOLS, KNOWLEDGE_TOOLS


def text(value):
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def saved_tool_status(name, content):
    match = re.search(r"(?m)^Status: (\S+)", content)
    if match:
        return match[1]
    try:
        control = json.loads(content)
        if isinstance(control, dict) and control.get("status") in {"not_submitted", "execution_unknown"}:
            return control["status"]
    except ValueError:
        pass
    # LLMAdapter deliberately omits a Status header for successful Read and
    # Knowledge replies. Rejections still use the explicit rejected header.
    return "ok" if name in (*READ_TOOLS, *KNOWLEDGE_TOOLS) else "unknown"


def replay_facts(path, *, observation_times=()):
    with path.open("rb") as stream:
        archive = MPQArchive(stream)
        header = latest().decode_replay_header(archive.header["user_data_header"]["content"])
        protocol = build(header["m_version"]["m_baseBuild"])
        details = protocol.decode_replay_details(archive.read_file("replay.details"))
        events = list(protocol.decode_replay_tracker_events(archive.read_file("replay.tracker.events")))
    own = next(e["m_playerId"] for e in events if e["_event"].endswith("SPlayerSetupEvent") and e.get("m_userId") == 0)
    units = {}
    created = defaultdict(Counter)
    lost = defaultdict(Counter)
    first_assets = defaultdict(dict)
    morphs, upgrades, stats, deaths = [], [], defaultdict(list), []
    requested = iter(observation_times)
    sample_time = next(requested, None)
    worker_samples = []
    def sample_workers(time):
        worker_samples.append({"seconds": time, "count": sum(
            unit["owner"] == own and unit["alive"] and unit.get("ready", True)
            and unit["name"] in {"SCV", "Probe", "Drone", "DroneBurrowed"}
            for unit in units.values())})
    for event in events:
        while sample_time is not None and round(sample_time * 22.4) < event["_gameloop"]:
            sample_workers(sample_time)
            sample_time = next(requested, None)
        kind = event["_event"].split(".")[-1]
        seconds = round(event["_gameloop"] / 22.4, 3)
        tag = (event.get("m_unitTagIndex"), event.get("m_unitTagRecycle"))
        if kind in ("SUnitBornEvent", "SUnitInitEvent"):
            owner = event.get("m_upkeepPlayerId", 0)
            name = text(event["m_unitTypeName"])
            if tag not in units:
                created[owner][name] += 1
            units[tag] = {"owner": owner, "name": name, "alive": True, "ready": kind == "SUnitBornEvent"}
            if kind == "SUnitBornEvent":
                first_assets[owner].setdefault(name, seconds)
        elif kind == "SUnitDoneEvent" and tag in units:
            unit = units[tag]
            unit["ready"] = True
            first_assets[unit["owner"]].setdefault(unit["name"], seconds)
        elif kind == "SUnitTypeChangeEvent" and tag in units:
            unit = units[tag]
            name = text(event["m_unitTypeName"])
            morphs.append({"seconds": seconds, "owner": unit["owner"], "from": unit["name"], "to": name})
            unit["name"] = name
            first_assets[unit["owner"]].setdefault(name, seconds)
        elif kind == "SUnitDiedEvent" and tag in units:
            unit = units[tag]
            unit["alive"] = False
            lost[unit["owner"]][unit["name"]] += 1
            deaths.append({"seconds": seconds, "owner": unit["owner"], "unit": unit["name"],
                           "killer": event.get("m_killerPlayerId")})
        elif kind == "SUpgradeEvent":
            upgrades.append({"seconds": seconds, "owner": event["m_playerId"],
                             "upgrade": text(event["m_upgradeTypeName"]), "count": event["m_count"]})
        elif kind == "SPlayerStatsEvent":
            value = event["m_stats"]
            stats[event["m_playerId"]].append({
                "seconds": seconds, "minerals": value.get("m_scoreValueMineralsCurrent"),
                "vespene": value.get("m_scoreValueVespeneCurrent"),
                "workers": value.get("m_scoreValueWorkersActiveCount"),
                "army_minerals": value.get("m_scoreValueMineralsUsedCurrentArmy"),
                "army_vespene": value.get("m_scoreValueVespeneUsedCurrentArmy"),
            })
    while sample_time is not None:
        sample_workers(sample_time)
        sample_time = next(requested, None)
    raw_result = details["m_playerList"][own - 1]["m_result"]
    own_result = Result(raw_result).name if raw_result else "Undecided"
    return {
        "base_build": header["m_version"]["m_baseBuild"], "own_player_id": own,
        "duration_seconds": header["m_elapsedGameLoops"] / 22.4, "own_result": own_result,
        "created_by_player": {str(k): dict(v) for k, v in created.items() if k},
        "lost_by_player": {str(k): dict(v) for k, v in lost.items() if k},
        "first_completed_asset_by_player": {str(k): v for k, v in first_assets.items() if k},
        "morphs": [m for m in morphs if m["owner"]], "upgrades": upgrades,
        "stats_by_player": {str(k): v for k, v in stats.items()}, "deaths": [d for d in deaths if d["owner"]],
        "worker_samples": worker_samples,
    }


def termination_checks(summary, episode_config, replay):
    limit = episode_config.get("game_time_limit_seconds")
    seconds = summary.get("game_time_seconds")
    natural = summary.get("end_reason") == "game_ended"
    timed_out = summary.get("end_reason") == "time_limit"
    valid_limit = type(limit) in (int, float) and 0 < limit < float("inf")
    valid_time = type(seconds) in (int, float) and 0 <= seconds < float("inf")
    # The Sharpy bridge enforces the limit on every frame; allow one frame-step
    # of clock quantization. A replay's leave result is not the timeout ruling.
    within_limit = valid_time and (limit is None or (valid_limit and seconds <= limit + 1.0))
    timeout_valid = valid_limit and valid_time and limit <= seconds <= limit + 1.0
    result = str(summary.get("result") or "").split(".")[-1]
    return {
        "episode_completed": summary.get("status") == "completed" and (natural or (timed_out and timeout_valid)),
        "configured_time_limit_respected": within_limit,
        "outcome_consistent_with_end_reason": (natural and result == replay.get("own_result"))
            or (timed_out and timeout_valid and result == "Tie"),
    }


def audit(root):
    manifest = json.loads((root / "run.json").read_text(encoding="utf-8"))
    if manifest["status"] == "running":
        raise ValueError("Game still running; audit after finalization")
    episode_dir = Path(manifest["record_directory"])
    episode = read_episode(episode_dir)
    log = (episode_dir / "log.txt").read_text(encoding="utf-8")
    calls = episode["metrics"]["model_calls"]
    pending, tools, problems, observations = {}, [], [], []
    now = 0
    model_replies = 0
    missing_log_bodies = []
    for index, message in enumerate(episode["messages"]):
        content = message.get("content") or ""
        if message["role"] == "user":
            if pending:
                problems.append({"message": index, "reason": "user_before_tool_results"})
            if content.startswith("[Current Observation]"):
                match = re.search(r"(?m)^Time (\d+):(\d+)", content)
                now = int(match[1]) * 60 + int(match[2]) if match else now
                worker = re.search(r"Workers \([^)]+\): (\d+)", content)
                observations.append({"seconds": now, "message_index": index,
                                     "workers": int(worker[1]) if worker else None})
        elif message["role"] == "assistant":
            model_replies += 1
            if content and content not in log:
                missing_log_bodies.append(index)
            if pending:
                problems.append({"message": index, "reason": "assistant_before_tool_results"})
            for call in message.get("tool_calls") or []:
                function = call["function"]
                try:
                    arguments = json.loads(function["arguments"])
                except ValueError:
                    arguments = function["arguments"]
                pending[call["id"]] = {"seconds": now, "name": function["name"], "arguments": arguments,
                                      "assistant_message_index": index, "call_id": call["id"]}
        elif message["role"] == "tool":
            call = pending.pop(message["tool_call_id"], None)
            if call is None:
                problems.append({"message": index, "reason": "unmatched_tool_result"})
                continue
            tools.append({**call, "status": saved_tool_status(call["name"], content),
                          "result": content, "tool_message_index": index})
    engine_path = episode_dir / "evaluation.jsonl.gz"
    engine_rows = list(iter_engine_samples(engine_path))
    engine_events = [e["event"] for e in engine_rows if e["kind"] == "platform_event"]
    steps = [e for e in engine_events if e["type"] == "step"]
    exact_times = [0] + [e["game_time_after_seconds"] for e in steps[:-1]]
    try:
        replay = replay_facts(episode_dir / "replay.SC2Replay", observation_times=exact_times)
    except (OSError, ValueError) as error:
        replay = {"error": type(error).__name__}
    summary = episode["summary"]
    settings = manifest["agent_metadata"]["settings"]
    worker_mismatches = [{"decision": index, "seconds": native["seconds"],
                          "delivered": observed["workers"], "native": native["count"],
                          "observation_message": observed["message_index"]}
                         for index, (observed, native) in enumerate(zip(observations, replay.get("worker_samples", [])))
                         if observed["workers"] != native["count"]]
    checks = {
        **termination_checks(summary, manifest["episode_config"], replay),
        "no_decision_cap": manifest["max_decisions"] is None,
        "no_model_call_or_output_caps": all(settings.get(name) is None for name in (
            "max_calls_per_decision", "max_calls_per_episode", "max_output_tokens", "max_episode_output_tokens")),
        "full_session_requested": settings.get("conversation_mode") == "full_session",
        "no_cross_episode_memory": settings.get("cross_episode_memory") is False,
        "single_full_session": sum(m["role"] == "system" for m in episode["messages"]) == 1,
        "complete_tool_pairs": not pending and not problems,
        "all_observations_retained": len(observations) == summary["decision_count"],
        "worker_inventory_matches_native_replay": len(observations) == len(replay.get("worker_samples", [])) and not worker_mismatches,
        "successful_calls_match_saved_replies": sum(c["status"] == "ok" for c in calls) == model_replies,
        "model_bodies_in_log": not missing_log_bodies,
        "provider_reasoning_in_log": all((m.get("reasoning_content") or "") in log
            for m in episode["messages"] if m["role"] == "assistant"),
        "statistics_kept_out_of_log": "[Model call statistics]" not in log,
        "session_tool_statuses_match_platform_metrics": [
            (t["name"], t["status"]) for t in tools if t["status"] not in {"not_submitted", "execution_unknown"}
        ] == [(t["name"], t["status"]) for t in episode["metrics"]["tool_calls"]],
        "replay_duration_matches_platform": isinstance(replay.get("duration_seconds"), (int, float))
            and abs(summary["game_time_seconds"] - replay["duration_seconds"]) < 2,
    }
    if settings.get("native_thinking_requested"):
        checks["thinking_responses_saved"] = any(m.get("reasoning_content")
            for m in episode["messages"] if m["role"] == "assistant")
    harness = settings["harness"]
    log_calls = [(role, float(clock)) for role, clock in re.findall(
        r"(?m)^\[Model call #\d+ \| (\w+) \| game_time=([\d.]+)s\]$", log)]
    role_counts = Counter(c["role"] for c in calls)
    auxiliary = accepted_auxiliary_replies(episode["messages"], calls)
    if harness == "act_only":
        checks["act_only_has_no_analysis_text"] = all(not (m.get("content") or "").strip()
            for m in episode["messages"] if m["role"] == "assistant")
        checks["act_only_has_no_auxiliary_roles"] = set(role_counts) <= {"actor"}
        checks["act_only_native_tools_required"] = manifest["agent_metadata"]["settings"].get("tool_choice") == "required"
    elif harness == "plan_and_execute":
        checks["planning_roles_present"] = all(role_counts.get(r, 0) for r in ("planner", "replanner", "executor"))
        checks["planner_only_at_start"] = all(clock == 0 for role, clock in log_calls if role == "planner")
        checks["replanner_only_after_advance"] = all(clock > 0 for role, clock in log_calls if role == "replanner")
        previous = None
        plan_errors = []
        focus_errors, execution_reference_errors = [], []
        current_policy = manifest["agent_metadata"]["settings"].get("executor_task_policy")
        for index, item in enumerate(auxiliary):
            try:
                plan, _ = validate_plan(item["content"], previous)
                if current_policy == "first_remaining_plan_step":
                    if not item["next_instruction"].endswith("Your current task is step 1: " + plan[0]):
                        focus_errors.append(index)
                    if item["role"] == "replanner":
                        marker = "Latest executor attempt (tool replies are receipts, not proof of completion): "
                        raw = item["instruction"].split(marker, 1)[1]
                        attempt = json.JSONDecoder().raw_decode(raw)[0]
                        observed = episode["messages"][attempt["observation_message_index"]]
                        valid = (previous and attempt["task"] == previous[0]
                                 and attempt["observed_at"] > attempt["submitted_at"]
                                 and observed["role"] == "user"
                                 and (observed.get("content") or "").startswith("[Current Observation]")
                                 and bool(attempt["tool_results"]))
                        tool_by_index = {t["tool_message_index"]: t for t in tools}
                        for result in attempt["tool_results"]:
                            actual = tool_by_index.get(result["result_message_index"], {})
                            valid = valid and actual.get("name") == result["tool"] and actual.get("status") == result["status"]
                        if not valid:
                            execution_reference_errors.append(index)
                previous = plan
            except (ValueError, TypeError) as error:
                plan_errors.append({"index": index, "error": str(error)})
            except (KeyError, IndexError) as error:
                execution_reference_errors.append({"index": index, "error": type(error).__name__})
        checks["accepted_plans_have_valid_progress_references"] = bool(auxiliary) and not plan_errors
        if current_policy == "first_remaining_plan_step":
            checks["executors_assigned_first_remaining_step"] = bool(auxiliary) and not focus_errors
            checks["execution_feedback_references_real_tools_and_new_observation"] = not execution_reference_errors
        checks["one_accepted_plan_per_decision"] = len(auxiliary) == summary["decision_count"]
    elif harness == "reflection":
        reviews = [a["content"] for a in auxiliary if a["role"] == "reflector"]
        checks["reflection_roles_present"] = role_counts.get("actor", 0) > 0 and role_counts.get("reflector", 0) > 0
        checks["reflection_only_after_advance"] = all(clock > 0 for role, clock in log_calls if role == "reflector")
        checks["accepted_reflections_are_text_only"] = bool(reviews) and all(s.strip() and not contains_tool_markup(s) for s in reviews)
        checks["one_accepted_reflection_per_new_decision"] = len(reviews) == max(0, summary["decision_count"] - 1)
    deltas = [e for e in engine_rows if e["kind"] == "engine_delta"]
    feedback_events = [event for step in engine_events if step.get("type") == "step"
                       for event in step.get("feedback", {}).get("events", [])]
    own = str(replay.get("own_player_id"))
    mule_confirmed = sum(e.get("type") == "call_mule" for e in feedback_events)
    checks["mule_confirmations_have_native_entities"] = mule_confirmed <= replay.get("created_by_player", {}).get(own, {}).get("MULE", 0)
    output = {
        "record_directory": str(episode_dir), "passed": all(checks.values()), "checks": checks,
        "summary": summary, "model_call_count": len(calls), "model_roles": dict(Counter(c["role"] for c in calls)),
        "harness": harness, "accepted_auxiliary_outputs": len(auxiliary),
        "assistant_text_reply_count": sum(m["role"] == "assistant" and bool((m.get("content") or "").strip()) for m in episode["messages"]),
        "model_call_failures": [c for c in calls if c["status"] != "ok"],
        "tool_calls_by_name": dict(Counter(c["name"] for c in tools)),
        "rejected_tools": [t for t in tools if t["status"] == "rejected"],
        "morph_requests": [t for t in tools if t["name"] == "morph_townhall"],
        "military_commands": [t for t in tools if t["name"] in ("dispatch_group", "attack_group", "defend_group", "reinforce_group", "retreat", "combat", "merge_groups")],
        "observations": observations, "message_pair_problems": problems, "missing_log_bodies": missing_log_bodies,
        "worker_count_mismatches": worker_mismatches,
        "execution_reference_errors": execution_reference_errors if harness == "plan_and_execute" else [],
        "engine_samples": len(deltas), "engine_last_time": deltas[-1]["game_time_seconds"] if deltas else None,
        "engine_event_types": dict(Counter(e["type"] for e in engine_events)), "replay": replay,
    }
    (root / "audit.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record_root", type=Path)
    args = parser.parse_args(argv)
    result = audit(args.record_root)
    print(json.dumps({"passed": result["passed"], "checks": result["checks"],
                      "result": result["summary"]["result"], "seconds": result["summary"]["game_time_seconds"],
                      "decisions": result["summary"]["decision_count"], "calls": result["model_call_count"],
                      "rejected_tools": len(result["rejected_tools"])}, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
