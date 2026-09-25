"""Agent-side rules for the query and action tool loop."""

from __future__ import annotations

from sc2bench_env.interface.tools import ACTION_TOOLS, KNOWLEDGE_TOOLS, READ_TOOLS


MAX_TOOL_ROUNDS = 24
TOOL_NOTE_RULE = (
    "Before any reply that calls tools, write one short note in the reply content: "
    "which facts you are using and why these calls are next. "
    "Do this for query replies and action replies. The note is not an action."
)
TOOL_NOTE_HINT = (
    "This reply was not submitted because it called tools without a content note. "
    "Write a short note first: which fact you are using and why this call is next. "
    "Then call the tools again."
)
NO_TOOL_HINT = (
    "Text without tool calls does not submit actions. "
    "Put the short content note in the same reply as the tool calls. "
    "Use additional query replies when more information is needed. "
    "When you act, put every action for this decision in one reply and end it with exactly one advance."
)
ACTION_BATCH_HINT = (
    "This reply was not submitted. Finish queries before acting. "
    "A query reply contains only query tools. "
    "An action reply contains only actions, includes every action for this decision, "
    "and ends with exactly one advance."
)

_QUERY_TOOLS = frozenset(READ_TOOLS) | frozenset(KNOWLEDGE_TOOLS)
_ACTION_TOOL_NAMES = frozenset(ACTION_TOOLS)


def action_names(schemas) -> frozenset[str]:
    """Actions offered for this race, including race-specific verbs."""
    names = set(_ACTION_TOOL_NAMES)
    for schema in schemas or ():
        function = schema.get("function") if isinstance(schema, dict) else None
        if isinstance(function, dict) and function.get("name"):
            names.add(str(function["name"]))
    return frozenset(names - _QUERY_TOOLS)


def reply_kind(names: list[str], available_actions: frozenset[str]) -> str:
    """Classify one model reply as a query round, action batch, or invalid."""
    if names and all(name in _QUERY_TOOLS for name in names):
        return "query"
    if (
        names
        and all(name in available_actions for name in names)
        and names.count("advance") == 1
        and names[-1] == "advance"
    ):
        return "action"
    return "invalid"
