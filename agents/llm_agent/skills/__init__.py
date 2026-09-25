"""Discover and load the race-specific strategy prompts shipped with llm_agent."""

from __future__ import annotations

from pathlib import Path
from typing import Optional


_SKILL_DIR = Path(__file__).resolve().parent
DEFAULT_SKILL = "none"


def _discover_skills() -> tuple[str, ...]:
    return tuple(sorted(
        path.relative_to(_SKILL_DIR).with_suffix("").as_posix()
        for path in _SKILL_DIR.glob("*/*.md")
    ))


AVAILABLE_SKILLS = _discover_skills()


def skill_race(name: str) -> str:
    """Return the race encoded by a canonical ``race/strategy`` skill id."""
    normalized = str(name or "").strip().lower().replace("\\", "/")
    return normalized.partition("/")[0]


def load_skill(name: Optional[str] = DEFAULT_SKILL) -> tuple[str, str] | None:
    """Return ``(canonical_name, text)`` or ``None`` for the no-skill baseline."""
    normalized = str(name or "none").strip().lower().replace("\\", "/")
    if normalized == "none":
        return None
    if normalized not in AVAILABLE_SKILLS:
        raise ValueError(
            f"unknown llm_agent skill {name!r}; available={list(AVAILABLE_SKILLS)}"
        )
    path = _SKILL_DIR / Path(*normalized.split("/")).with_suffix(".md")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"llm_agent skill {normalized!r} is empty")
    return normalized, text
