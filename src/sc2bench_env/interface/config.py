"""Episode configuration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from .opponents import normalize_opponent, require_enemy_style


@dataclass(frozen=True)
class EpisodeConfig:
    """Configuration for one environment episode.

    Sharpy uses map/opponent/race and game-clock limits. FakeBackend models the
    public contract, not an actual map or opponent. The optional seed remains
    an experimental backend argument; Benchmark Suites do not use it.
    """

    race: str = "terran"
    enemy_race: str = "terran"
    map_name: str = "KairosJunctionLE"
    opponent: str = "easy"
    game_time_limit_seconds: Optional[float] = 600.0
    seed: Optional[int] = None
    # Freeze simulation between reset/step returns and the next valid decision.
    # False preserves the continuously running, asynchronous game mode.
    blocking_decisions: bool = True
    # Builtin AI's SC2 AIBuild, independent of its difficulty and race.
    enemy_style: str = "random"

    def __post_init__(self) -> None:
        object.__setattr__(self, "opponent", normalize_opponent(self.opponent))
        require_enemy_style(self.enemy_style)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
