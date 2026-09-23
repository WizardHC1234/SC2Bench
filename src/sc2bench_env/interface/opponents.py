"""SC2 difficulty names, matching Commander's ten enum levels.

This registry is platform-owned and does not import Commander or SC2.
Episode and suite IDs use the short names below.
"""

DIFFICULTY_ENUM_NAMES = {
    "veryeasy": "VeryEasy",
    "easy": "Easy",
    "medium": "Medium",
    "mediumhard": "MediumHard",
    "hard": "Hard",
    "harder": "Harder",
    "veryhard": "VeryHard",
    "cheatvision": "CheatVision",
    "cheatmoney": "CheatMoney",
    "cheatinsane": "CheatInsane",
}
BUILTIN_OPPONENTS = tuple(DIFFICULTY_ENUM_NAMES)
AI_BUILD_ENUM_NAMES = {
    "random": "RandomBuild", "rush": "Rush", "timing": "Timing",
    "power": "Power", "macro": "Macro", "air": "Air",
}
ENEMY_STYLES = tuple(AI_BUILD_ENUM_NAMES)
_ALIASES = {"vision": "cheatvision", "money": "cheatmoney", "insane": "cheatinsane"}


def normalize_opponent(value: str) -> str:
    """Return a short ID; accept reviewed aliases only."""
    if not isinstance(value, str):
        raise ValueError("opponent must be a difficulty name")
    name = value.strip().lower()
    canonical = _ALIASES.get(name, name)
    if canonical not in DIFFICULTY_ENUM_NAMES:
        raise ValueError(f"unsupported opponent {value!r}; expected one of {BUILTIN_OPPONENTS}")
    return canonical


def require_enemy_style(value: str) -> str:
    """Validate the canonical builtin AI build style; never silently fall back."""
    if not isinstance(value, str) or value not in AI_BUILD_ENUM_NAMES:
        raise ValueError(f"unsupported enemy_style {value!r}; expected one of {ENEMY_STYLES}")
    return value


def normalize_enemy_style(value: str) -> str:
    """CLI accepts case/whitespace variation; episode/Suite IDs stay canonical."""
    if not isinstance(value, str):
        raise ValueError("enemy_style must be a style name")
    return require_enemy_style(value.strip().lower())
