"""Internal deterministic production lifecycle fixture; not a public example."""
from __future__ import annotations

from tests.helpers.drive import drive
from sc2bench_env.interface.observations import observation_lines
import json
from sc2bench_env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.observation_text import render_feedback_text

def run_episode(
    env: Environment, config: EpisodeConfig | None = None, *,
    max_decisions: int = 100, show_observation: bool = False,
) -> dict:
    """Start once, submit each order once, then wait for the actual terminal.

    The caller owns env and must close it in finally. Existing tasks continue
    automatically; do not resubmit the build/train orders while waiting.
    """
    if type(max_decisions) is not int or max_decisions < 1:
        raise ValueError("max_decisions must be a positive integer")
    observation = env.reset(config or EpisodeConfig(game_time_limit_seconds=240))
    print(f"record_dir={env.record_path}", flush=True)
    if show_observation:
        print("\n".join(observation_lines(observation)), flush=True)

    # This deterministic smoke intentionally tests only one production chain.
    plan = (
        [{"name": "build", "arguments": {'target': "supply_depot"}}],
        [{"name": "build", "arguments": {'target': "barracks"}}],
        [{"name": "train", "arguments": {'target': "marine", 'count': 4}}],
    )
    for index in range(max_decisions):
        commands = plan[index] if index < len(plan) else []
        decision = [*commands, {"name": "advance", "arguments": {'seconds': 5}}]
        observation, feedback, terminated, info = drive(env,
            decision, agent_context={"agent_kind": "scripted_smoke"},
        )
        print(
            f"round={index + 1} game_seconds={observation.game.game_time_seconds:.1f} "
            f"commands={json.dumps(commands)} terminated={terminated}", flush=True,
        )
        if feedback.receipts or feedback.events:
            print(render_feedback_text(feedback.to_dict()), flush=True)
        if show_observation:
            print("\n".join(observation_lines(observation)), flush=True)
        if terminated:
            return info
        if "error" in info:
            # Never advance the plan after a rejected script command.
            raise RuntimeError("Scripted decision rejected; inspect feedback and update the example")
    env.close(end_reason="decision_limit")
    return {"result": None, "end_reason": "decision_limit"}
