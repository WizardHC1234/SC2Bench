"""Test helper that speaks the tool loop, then steps a DecisionBatch."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import time

from sc2bench_env.interface.actions import ActionValidationError, DecisionBatch, parse_decision
from sc2bench_env.interface.agent import AgentTurn
from sc2bench_env.interface.tools import ToolCall


def calls_to_batch(calls: Sequence[Mapping[str, Any]], *, race: str = "terran") -> DecisionBatch:
    return parse_decision(list(calls), race=race)


def drive(env, calls, **kwargs):
    """Submit one decision the way an agent would, then step."""
    if isinstance(calls, DecisionBatch):
        return env.step(calls, **kwargs)
    race = env.config.race if getattr(env, "config", None) is not None else "terran"
    try:
        batch = calls_to_batch(calls, race=race)
    except ActionValidationError as exc:
        snapshot = env.backend.snapshot()
        return env._record_rejected_decision(
            exc, snapshot, submitted=list(calls),
            agent_context=kwargs.get("agent_context"),
            started=time.perf_counter(),
            before=snapshot.game_time_seconds,
        )
    return env.step(batch, **kwargs)


def submit_calls(manager, calls, **kwargs):
    race = kwargs.pop("race", "terran")
    return manager.submit_decision(calls_to_batch(calls, race=race), **kwargs)


def query(env, call: ToolCall):
    """One read on its own turn. The turn is retired so the next query can start."""
    turn = env.begin_tool_turn()
    try:
        return turn.call(call).data
    finally:
        turn.abort()


def play(match, player: int, calls, **kwargs):
    if player not in match._tool_turns:
        match.request(player)
    turn = match._tool_turns[player]
    for item in calls:
        arguments = item.get("arguments") if isinstance(item, Mapping) else {}
        turn.call(ToolCall(item["name"], dict(arguments or {})))
    batch = match.finish_turn(player)
    return match.submit(player, batch, **kwargs)


def rendered_messages(request) -> list[dict[str, str]]:
    from sc2bench_env.adapters.llm import LLMAdapter

    race = str(getattr(request.observation.game, "race", None) or "terran")
    adapter = LLMAdapter(race=race)
    return [
        {"role": "system", "content": adapter.system_prompt()},
        {"role": "user", "content": adapter.render_input(request)},
    ]


def scripted_advance(request, seconds: float = 1, content: str = "wait") -> AgentTurn:
    request.call_tool(ToolCall("advance", {"seconds": seconds}))
    return AgentTurn({
        "messages": rendered_messages(request),
        "assistant_content": content,
    })
