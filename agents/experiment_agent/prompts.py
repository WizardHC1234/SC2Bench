"""Harness instructions only; race rules and tool semantics come from the platform."""
from dataclasses import replace
import hashlib
import json

from sc2bench_env.interface.platform_prompt import default_prompt_parts, prompt_part_hashes

COMMON = (
    "Use only information delivered in this game's observations and tool results. "
    "The newest observation supersedes older state; missing enemy information is not proof of destruction. "
    "All history, plans and reflections belong to this game only. "
    "Use native function tools for operations; prose never executes an operation. "
    "Queries and actions may be interleaved. Submit advance only as the final call "
    "of a reply. Once advance returns decision_ready, wait for the next observation. "
    "Staged work has not started; evaluate outcomes using observations after game time advances. "
    "A rejected or no-op call does not undo earlier staged work. Correct only what needs correction. "
    "Do not repeat a persistent task merely because it is still pending: another request adds work. "
    "Choose actions from the delivered state, not from a prescribed build order."
)
GUIDANCE = {
    "act_only": "Select tools directly. Return tool calls with empty message content. Do not write analysis, plans or reflection.",
    "react": "Work in a Thought / Action / Observation loop. Briefly explain the current decision in message content, call tools, then use their returned observations to choose the next action.",
    "plan_and_execute": "A planner maintains an ordered plan, an executor uses a ReAct tool loop, and a replanner updates the plan from new game observations. Follow the current task instructions. A step may span several game advances; only mark progress supported by delivered observations.",
    "reflection": "Use a ReAct tool loop. After game time advances, a separate reflection stage reviews actual observations and execution feedback before the next action. Apply those within-game lessons in subsequent decisions. Do not use previous games or retry an episode.",
}
PLAN = (
    "Produce an ordered plan for this game using the delivered state. "
    'Return only JSON: {"plan": ["step", "step"], "completed_steps": []}. '
    "The plan must be a nonempty ordered list of distinct, actionable steps with observable completion. "
    "Base it on the current state; do not assume unobserved assets or completed prerequisites. "
    "completed_steps must be empty on this initial turn. Do not call tools."
)
REPLAN = (
    "Review the previous plan against the latest observation and feedback. "
    "Keep unfinished useful steps and revise steps when justified. "
    'Return only JSON: {"plan": ["remaining step", "next step"], '
    '"completed_steps": ["previous step verified complete in delivered observations"]}. '
    "completed_steps refers only to newly verified progress; it may be empty. "
    "Copy each completed step exactly from Previous remaining plan; do not paraphrase it or "
    "also include it in the remaining plan. Return exactly these two JSON fields. "
    "An omitted, abandoned or unaffordable step is not a completed step. "
    "If completion is not established by observations, leave it out of completed_steps. "
    "Keep a nonempty remaining plan while the game continues. Do not declare the game won or call tools."
)
REFLECT = (
    "Game time has advanced. Review the previous decisions against "
    "the new observation and feedback. Give a concise evidence-based reflection: "
    "what changed, what worked or failed, and what to keep or adjust. Distinguish "
    "unstarted persistent work from failed execution. Do not infer hidden enemy state. "
    "You are the reviewer, not the acting agent. Return only a prose assessment and next focus. "
    "Do not emit function calls, DSML/XML tool markup, or claim to submit new actions. "
    "These lessons apply only to the current game."
)
ACT = (
    "Select the next tools using the current observation and this game's history. "
    "Give a brief decision rationale tied to the delivered state, call tools, and use their results "
    "before choosing subsequent actions. "
    "Plans and reflections are advice, not executed actions. Only native tool results confirm "
    "submitted work; check the current observation and outstanding work before acting."
)
ACT_ONLY = (
    "Return native function tool calls ONLY, with empty message content. "
    "Do not write any natural-language introduction, Thought, analysis, explanation, plan or reflection. "
    "This also applies after queries and when correcting tool arguments. "
    "Select the next tool calls directly from the delivered observation and tool results."
)
EXECUTE = (
    "Use a brief Thought / Action / Observation tool loop to advance the "
    "first remaining plan step, identified below as your current task. "
    "Focus on this task and its prerequisites; do not independently choose a different future step. "
    "You may maintain necessary ongoing production and respond to immediate threats. "
    "If the task needs time, advance and use the next observation; advance does not finish a step. "
    "The current observation and tool results override any mistaken plan assumption. "
    "Check outstanding work before adding requests. Staged work is not completed work. "
    "Plans and reflections do not execute tools; submit actual native calls and review their results."
)
NO_TOOLS = "No tool call was submitted. Choose a native tool call, including advance when no new action is needed."
TEXT_ONLY_STAGE = "This stage accepts text only. Do not call tools; follow the requested response format."
TOOL_MARKUP_REPAIR = (
    "The previous text contained encoded tool calls. None of those calls was executed. "
    "Follow the current text-only stage's response format; "
    "do not emit tool calls, DSML/XML, or claim to submit actions."
)
BAD_PLAN = 'Return only valid JSON with a nonempty string list "plan" and a string list "completed_steps".'
ACT_ONLY_REPAIR = "Act-only requires empty message content and native tool calls. The previous tool calls were not submitted."


def stage_repair(instruction, feedback):
    return instruction + "\n[Harness feedback] " + feedback


def prompt_parts(race, harness):
    return replace(default_prompt_parts(race), decision_guidance=COMMON + "\n" + GUIDANCE[harness])


def fingerprints():
    """Hash every static stage instruction, not just the actor's system prompt."""
    stages = {name: globals()[name] for name in (
        "PLAN", "REPLAN", "REFLECT", "ACT", "ACT_ONLY", "EXECUTE", "NO_TOOLS",
        "TEXT_ONLY_STAGE", "TOOL_MARKUP_REPAIR", "BAD_PLAN", "ACT_ONLY_REPAIR",
    )}
    return {
        "stages_sha256": hashlib.sha256(json.dumps(stages, sort_keys=True).encode()).hexdigest(),
        "platform_parts": {
            race: {h: prompt_part_hashes(prompt_parts(race, h)) for h in GUIDANCE}
            for race in ("terran", "protoss", "zerg")
        },
    }
