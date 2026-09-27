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
NO_TOOL_HINT = (
    "Text without tool calls does not act. "
    "Call the tools you need and use each result before the next call. "
    "Queries and actions may be interleaved. "
    "Call advance last to submit the staged actions and move game time."
)


def platform_turn_messages(observation, feedback, race: str = "terran"):
    """Example harness messages. Tests use this in place of Environment.get_context."""
    adapter = LLMAdapter(race=race)
    request = AgentInput(observation, feedback)
    return [
        {"role": "system", "content": adapter.system_prompt(race)},
        {"role": "user", "content": adapter.render_input(request)},
    ]
