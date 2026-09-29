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
    # Structured Observation may still keep this turn's events for records.
    # Text Recent Events must stay historical so Previous Feedback is the only
    # place the agent reads events created during the last advance.
    text_observation = dict(observation)
    if feedback is not None:
        feedback_events = [
            event for event in feedback.get("events", []) if isinstance(event, dict)
        ]
        feedback_ids = {
            event.get("event_id") for event in feedback_events
            if event.get("event_id") is not None
        }
        remaining = [event for event in feedback_events if event.get("event_id") is None]
        historical = []
        for event in list(observation.get("recent_events") or []):
            event_id = event.get("event_id") if isinstance(event, dict) else None
            if event_id is not None and event_id in feedback_ids:
                continue
            if event_id is None and event in remaining:
                remaining.remove(event)
                continue
            historical.append(event)
        text_observation["recent_events"] = historical
    user = "[Current Observation]\n" + render_observation_text(text_observation, previous=previous)
    if feedback is not None:
        user += "\n\n[Previous Feedback]\n" + render_feedback_text(feedback)
    if not observation.get("terminated", False):
        user += "\n\n" + DECISION_REQUEST
    return [{"role": "system", "content": prompt}, {"role": "user", "content": user}]
