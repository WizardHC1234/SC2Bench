"""Neutral prompt modules. Harnesses may replace a part without copying the rest."""
from __future__ import annotations


from dataclasses import dataclass

from sc2bench_env.interface.platform_rules import (
    ARMY_RULES,
    CONTROL_RULES,
    DECISION_REQUEST,
    EXECUTION_RULES,
    GAME_RULES,
    INTERACTION_RULES,
    PLANNING_RULES,
    PROTOSS_CONTROL_RULES,
    PROTOSS_GAME_RULES,
    PROTOSS_ROLE_RULES,
    ROLE_RULES,
    ZERG_CONTROL_RULES,
    ZERG_GAME_RULES,
    ZERG_ROLE_RULES,
)
from sc2bench_env.interface.races import require_supported_own_race


@dataclass(frozen=True)
class PromptParts:
    role_and_objective: str
    game_rules: str
    observation_rules: str
    tool_interaction: str
    execution_rules: str
    decision_guidance: str


def default_prompt_parts(race: str = "terran") -> PromptParts:
    """Platform facts for one race. decision_guidance is the replaceable reminder."""
    require_supported_own_race(race)
    from sc2bench_env.catalog.registry import render_observation_guide

    if race == "protoss":
        role = (
            "You are an autonomous StarCraft II agent controlling protoss through SC2Bench.\n"
            + PROTOSS_ROLE_RULES
        )
        execution = PROTOSS_CONTROL_RULES + "\n" + PLANNING_RULES + "\n" + EXECUTION_RULES + "\n" + ARMY_RULES
        game = PROTOSS_GAME_RULES
    elif race == "zerg":
        role = (
            "You are an autonomous StarCraft II agent controlling zerg through SC2Bench.\n"
            + ZERG_ROLE_RULES
        )
        execution = ZERG_CONTROL_RULES + "\n" + PLANNING_RULES + "\n" + EXECUTION_RULES + "\n" + ARMY_RULES
        game = ZERG_GAME_RULES
    else:
        role = (
            f"You are an autonomous StarCraft II agent controlling {race} through SC2Bench.\n"
            + ROLE_RULES
        )
        execution = CONTROL_RULES + "\n" + PLANNING_RULES + "\n" + EXECUTION_RULES + "\n" + ARMY_RULES
        game = GAME_RULES
    return PromptParts(
        role_and_objective=role.strip(),
        game_rules=game.strip(),
        observation_rules=render_observation_guide().strip(),
        tool_interaction=INTERACTION_RULES.strip(),
        execution_rules=execution.strip(),
        decision_guidance=DECISION_REQUEST.strip(),
    )


def render_prompt(parts: PromptParts) -> str:
    sections = (
        ("1. Role and objective", parts.role_and_objective),
        ("2. Game basics", parts.game_rules),
        ("3. Reading Observation", parts.observation_rules),
        ("4. Tool interaction", parts.tool_interaction),
        ("5. Platform execution model", parts.execution_rules),
        ("6. Decision guidance", parts.decision_guidance),
    )
    return "\n\n".join(f"{heading}\n{body.rstrip()}" for heading, body in sections) + "\n"


def prompt_part_hashes(parts: PromptParts) -> dict[str, str]:
    import hashlib

    return {
        name: hashlib.sha256(getattr(parts, name).encode("utf-8")).hexdigest()
        for name in (
            "role_and_objective", "game_rules", "observation_rules",
            "tool_interaction", "execution_rules", "decision_guidance",
        )
    }
