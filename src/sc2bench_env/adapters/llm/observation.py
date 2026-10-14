"""Observation and feedback text used by the LLM adapter."""

from typing import Any, Dict, List, Optional

from sc2bench_env.interface.observation_text import render_feedback_text, render_text
from sc2bench_env.interface.observations import render_brief_observation_text

__all__ = [
    "platform_messages", "render_feedback_text", "render_brief_observation_text", "render_text",
]


def platform_messages(
    prompt: str, observation: Dict[str, Any], feedback: Optional[Dict[str, Any]] = None,
    previous: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    # Structured Observation keeps complete event history for records. The
    # turn text presents only meaningful events produced by the last advance:
    # execution changes belong in Changes, while receipts own submission state.
    # Advance / staging rules live in the Platform Prompt, not every OBS turn.
    text_observation = dict(observation)
    feedback_events: List[Dict[str, Any]] = []
    events_accounted_for: List[Dict[str, Any]] = []
    if feedback is not None:
        feedback_events = [
            event for event in feedback.get("events", []) if isinstance(event, dict)
        ]
        change_events = [
            event for event in feedback_events
            if event.get("type") not in {"demand_accepted", "decision_rejected"}
        ]
        text_observation["recent_events"] = change_events
        # Accepted submissions are already represented by receipts. Execution
        # changes are shown above. A decision_rejected event stays in Feedback
        # so its detailed reason remains visible.
        events_accounted_for = change_events + [
            event for event in feedback_events if event.get("type") == "demand_accepted"
        ]
    user = "[Current Observation]\n" + render_brief_observation_text(text_observation, previous=previous)
    if feedback is not None:
        feedback_text = render_feedback_text(
            feedback,
            shown_events=events_accounted_for,
            include_receipts=not bool(feedback.get("receipts_delivered")),
        )
        if feedback_text.strip():
            user += "\n\n[Previous Feedback]\n" + feedback_text
    return [{"role": "system", "content": prompt}, {"role": "user", "content": user}]
