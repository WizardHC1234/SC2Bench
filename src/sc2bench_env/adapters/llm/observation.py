"""Observation and feedback text used by the LLM adapter."""

from typing import Any, Dict, List, Optional

from sc2bench_env.interface.observation_text import render_feedback_text, render_text
from sc2bench_env.interface.observations import render_observation_text
from sc2bench_env.interface.platform_rules import DECISION_REQUEST

__all__ = [
    "platform_messages", "render_feedback_text", "render_observation_text", "render_text",
]


def platform_messages(
    prompt: str, observation: Dict[str, Any], feedback: Optional[Dict[str, Any]] = None,
    previous: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    user = "[Current Observation]\n" + render_observation_text(observation, previous=previous)
    if feedback is not None:
        user += "\n\n[Previous Feedback]\n" + render_feedback_text(
            feedback, shown_events=observation.get("recent_events", []))
    if not observation.get("terminated", False):
        user += "\n\n" + DECISION_REQUEST
    return [{"role": "system", "content": prompt}, {"role": "user", "content": user}]
