"""Example-harness reminders. Platform facts stay in PromptParts."""
from __future__ import annotations

from dataclasses import replace

from sc2bench_env.adapters.llm import LLMAdapter
from sc2bench_env.interface.agent import AgentInput
from sc2bench_env.interface.platform_prompt import default_prompt_parts


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
    "Example Agent decision process:\n"
    "- Read the current Observation and previous Feedback. Check Production Priority before adding work.\n"
    "- If accepted work no longer fits the current plan, cancel matching unstarted work by target action "
    "and target before adding replacement work. Do not cancel work merely because it is temporarily blocked.\n"
    "- Query missing static or map facts first and reuse facts already queried in this session. "
    "Queries may take multiple rounds.\n"
    "- Before staging work, check minerals, gas, supply, prerequisites and free production slots.\n"
    "- After querying, submit all chosen actions together in one tool-call reply without advance.\n"
    "- Every operation chosen in the reply text must appear as a tool call in that same reply; "
    "mentioning an operation in text does not execute it.\n"
    "- After their results return, call advance by itself to submit the decision and move game time.\n"
    "- Do not mix queries, actions and advance in the same reply."
)
EXAMPLE_AGENT_GUIDANCE = DECISION_FLOW + "\n\n" + TOOL_NOTE_RULE
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


def example_prompt_parts(race: str):
    """Replace the platform's optional decision guidance for this example Agent."""
    return replace(
        default_prompt_parts(race),
        decision_guidance=EXAMPLE_AGENT_GUIDANCE,
    )


def platform_turn_messages(observation, feedback, race: str = "terran"):
    """Example harness messages. Tests use this in place of Environment.get_context."""
    adapter = LLMAdapter(example_prompt_parts(race), race=race)
    request = AgentInput(observation, feedback)
    return [
        {"role": "system", "content": adapter.system_prompt()},
        {"role": "user", "content": adapter.render_input(request)},
    ]
