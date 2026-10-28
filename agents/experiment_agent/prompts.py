"""Harness instructions only; race rules and tool semantics come from the platform."""
from dataclasses import replace
import hashlib
import json

from sc2bench_env.interface.platform_prompt import default_prompt_parts, prompt_part_hashes

ROLE_INSTRUCTIONS = {
    "actor": "You are the acting player. Choose and submit game actions.",
    "executor": "You are the executor. Carry out the current plan task while managing the ongoing game.",
    "planner": "You are the planner. Develop the game's plan without submitting environment actions.",
    "replanner": "You are the replanner. Update the plan using execution feedback and the current observation.",
    "reflector": "You are the reviewer. Assess the observed results and give feedback without submitting environment actions.",
}

COMMON = (
    "Use the history, plans and reflections from the current game. "
    "Submit game actions through native function tools. "
    "Review the economy, supply, production queues, blockers, army orders and visible threats. "
    "Call advance on its own, in a separate reply, once this decision needs no further actions. "
    "Waiting without adding actions is also valid."
)
GUIDANCE = {
    "act_only": "Use direct action selection.",
    "react": "Use a Thought / Action / Observation loop.",
    "plan_and_execute": "Plan before execution and replan using observations after game time advances.",
    "reflection": "Review execution feedback after game time advances, then resume the ReAct action loop.",
}
PLAN = (
    "Produce an ordered plan toward winning the game using the current observation. "
    'Return only JSON: {"plan": ["step", "step"], "completed_steps": []}. '
    "The plan must be a nonempty ordered list of distinct, actionable steps with observable completion. "
    "Choose the planning horizon and level of detail appropriate to the situation. "
    "Account for ongoing economy, supply and production while pursuing the plan. "
    "Base it on the current state and distinguish confirmed conditions from assumptions. "
    "completed_steps must be empty on this initial turn. Do not call tools."
)
REPLAN = (
    "Review the previous plan against the latest observation and feedback. "
    "Keep useful unfinished steps and revise or replace steps as the situation requires. "
    "Choose the planning horizon and level of detail appropriate to the situation. "
    "Use actionable steps with observable completion conditions. "
    "Account for ongoing management alongside the plan. "
    'Return only JSON: {"plan": ["remaining step", "next step"], '
    '"completed_steps": ["previous step verified complete in delivered observations"]}. '
    "Return exactly these two fields. completed_steps may be empty; it contains only newly verified "
    "completed steps copied exactly from Previous remaining plan. Revised or abandoned steps are not "
    "completed, and completed steps cannot also remain in plan. "
    "Keep a nonempty remaining plan while the game continues. Do not call tools."
)
REFLECT = (
    "Review the previous decisions against "
    "the new observation and feedback. Give an evidence-based reflection: "
    "what changed, what worked or failed, and what to keep or adjust. "
    "Account for ongoing and blocked work, and for uncertainty in enemy information. "
    "Return your assessment and next focus in prose."
)
ACT = (
    "Select the next tools using the current observation and this game's history. "
    "Give a decision rationale tied to the delivered state, call tools, and use their results "
    "before choosing subsequent actions."
)
ACT_ONLY = (
    "Return native function tool calls only, with empty message content."
)
EXECUTE = (
    "Use a Thought / Action / Observation tool loop to advance the "
    "first remaining plan step, identified below as your current task. "
    "Alongside this task, manage economy, supply, production and immediate threats. "
    "Use the current observation, outstanding work and tool feedback to assess progress "
    "and choose further actions or waiting."
)
NO_TOOLS = "No tool call was submitted. Choose a native tool call, including advance when no new action is needed."
CONTINUE_ANALYSIS = "Continue reasoning as needed, then use tools to carry out the decision."
ADVANCE_SEPARATE = (
    "Submit the actions for this decision first and review their tool results. "
    "Call advance by itself in a separate reply when no further actions or corrections are needed."
)
TEXT_ONLY_STAGE = "This stage accepts text only. Do not call tools; follow the requested response format."
TOOL_MARKUP_REPAIR = (
    "Encoded tool calls in the previous text were not submitted. "
    "Return a response in the text format requested for this stage."
)
BAD_PLAN = 'Return only valid JSON with a nonempty string list "plan" and a string list "completed_steps".'
ACT_ONLY_REPAIR = "Act-only requires empty message content and native tool calls. The previous tool calls were not submitted."


def stage_repair(instruction, feedback):
    return instruction + "\n[Harness feedback] " + feedback


def prompt_parts(race, harness):
    parts = default_prompt_parts(race)
    return replace(parts,
                   role_and_objective=parts.role_and_objective.replace(
                       "an autonomous StarCraft II agent", "an expert StarCraft II player", 1),
                   decision_guidance=COMMON + "\n" + GUIDANCE[harness])


def fingerprints():
    """Hash every static stage instruction, not just the actor's system prompt."""
    stages = {name: globals()[name] for name in (
        "ROLE_INSTRUCTIONS",
        "PLAN", "REPLAN", "REFLECT", "ACT", "ACT_ONLY", "EXECUTE", "NO_TOOLS", "CONTINUE_ANALYSIS",
        "TEXT_ONLY_STAGE", "TOOL_MARKUP_REPAIR", "BAD_PLAN", "ACT_ONLY_REPAIR", "ADVANCE_SEPARATE",
    )}
    return {
        "stages_sha256": hashlib.sha256(json.dumps(stages, sort_keys=True).encode()).hexdigest(),
        "platform_parts": {
            race: {h: prompt_part_hashes(prompt_parts(race, h)) for h in GUIDANCE}
            for race in ("terran", "protoss", "zerg")
        },
    }
