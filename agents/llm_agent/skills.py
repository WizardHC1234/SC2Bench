"""Load the small set of strategy prompts shipped with llm_agent."""

from __future__ import annotations

from pathlib import Path
from typing import Optional


DEFAULT_SKILL = "tank"
AVAILABLE_SKILLS = (DEFAULT_SKILL,)
_SKILL_DIR = Path(__file__).with_name("skills")


def load_skill(name: Optional[str] = DEFAULT_SKILL) -> tuple[str, str] | None:
    """Return ``(canonical_name, text)`` or ``None`` for the no-skill baseline."""
    normalized = str(name or "none").strip().lower()
    if normalized == "none":
        return None
    if normalized not in AVAILABLE_SKILLS:
        raise ValueError(
            f"unknown llm_agent skill {name!r}; available={list(AVAILABLE_SKILLS)}"
        )
    path = _SKILL_DIR / f"{normalized}.md"
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"llm_agent skill {normalized!r} is empty")
    return normalized, text
