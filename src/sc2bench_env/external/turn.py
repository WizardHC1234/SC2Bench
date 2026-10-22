"""One decision's input. The external Agent keeps the session; we only send this turn."""
from __future__ import annotations


from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class TurnInput:
    turn_id: str
    observation: dict[str, Any]
    feedback: Optional[dict[str, Any]] = None
    marker: Optional[str] = None
    previous: Optional[dict[str, Any]] = None

    def render(self, *, first: bool) -> str:
        from sc2bench_env.adapters.llm.observation import platform_messages
        from sc2bench_env.adapters.llm.prompt import system_prompt

        briefing = platform_messages(
            "", self.observation, self.feedback, previous=self.previous,
        )[1]["content"]
        if self.marker:
            briefing += "\n\nmarker:\n" + self.marker
        if not first:
            return briefing
        game = self.observation.get("game") if isinstance(self.observation, dict) else None
        race = str(game.get("race")) if isinstance(game, dict) and game.get("race") else "terran"
        return system_prompt(race=race).rstrip() + "\n\n" + briefing


@dataclass
class AgentContext:
    mcp_url: str
    mcp_token: Optional[str]
    match_id: str
    player_id: str
    work_dir: str
    model: str
    reasoning_effort: str
    player_index: int = 0


@dataclass
class AgentHandle:
    """Opaque resume credential. SC2Bench does not read the Agent session."""

    opaque_resume_id: str
    adapter_type: str
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnRun:
    returncode: Optional[int] = 0
    usage: Optional[dict[str, Any]] = None
