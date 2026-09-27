"""Example-harness reminders. Platform facts stay in PromptParts."""
from __future__ import annotations


from sc2bench_env.adapters.llm import LLMAdapter
from sc2bench_env.interface.agent import AgentInput


MAX_TOOL_ROUNDS = 24
TOOL_NOTE_RULE = (
    "Before any reply that calls tools, write one short note in the reply content: "
    "which facts you are using and why these calls are next. "
    "The note is this example agent's own habit, not a platform requirement."
)
TOOL_NOTE_HINT = (
    "This reply was not submitted because it called tools without a content note. "
    "Write a short note first: which fact you are using and why this call is next. "
    "Then call the tools again."
)
DECISION_FLOW = (
    "For each decision, query any missing information first. Queries may take multiple rounds. "
    "After querying, submit all chosen actions together in one tool-call reply without advance. "
    "After their results return, call advance by itself to submit the decision and move game time. "
    "Do not mix queries, actions and advance in the same reply."
)
NO_TOOL_HINT = (
    "Text without tool calls does not act. "
    "Query any missing information, or submit this decision's actions together without advance. "
    "If no new action is needed, call advance by itself."
)
READY_TEXT_HINT = (
    "Text without tool calls does not act. "
    "Actions for this decision are already staged. "
    "Call advance by itself, or submit one correction batch of action tools only. "
    "Do not query again and do not repeat actions that are already staged."
)
MIXED_REPLY_HINT = (
    "This reply was not submitted because it mixes stages. "
    "One reply may contain only queries, or only actions, or advance by itself. "
    "Do not mix queries, actions, and advance."
)
QUERY_AFTER_ACTIONS_HINT = (
    "This reply was not submitted because this decision has already left the query stage. "
    "Call advance by itself, or submit one correction batch of action tools only. "
    "Do not query again and do not repeat actions that are already staged."
)


def platform_turn_messages(observation, feedback, race: str = "terran"):
    """Example harness messages. Tests use this in place of Environment.get_context."""
    adapter = LLMAdapter(race=race)
    request = AgentInput(observation, feedback)
    return [
        {"role": "system", "content": adapter.system_prompt()},
        {"role": "user", "content": adapter.render_input(request)},
    ]
