"""Episode configuration."""
from __future__ import annotations


from dataclasses import asdict, dataclass
import math
from typing import Any, Optional

from .opponents import normalize_opponent, require_enemy_style
from .races import require_supported_own_race, ENEMY_RACES


@dataclass(frozen=True)
class EpisodeConfig:
    """Configuration for one environment episode.

    Sharpy uses map/opponent/race and game-clock limits. FakeBackend models the
    public contract, not an actual map or opponent. The optional seed controls
    only the SC2 simulator. Suites may copy game_seeds onto this field. A seed
    does not control the model, harness, Python RNG, or external services.
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
    # While the model thinks, step at wall-clock speed. advance still fast-forwards.
    realtime: bool = False
    # Builtin AI's SC2 AIBuild, independent of its difficulty and race.
    enemy_style: str = "random"

    def __post_init__(self) -> None:
        object.__setattr__(self, "opponent", normalize_opponent(self.opponent))
        require_enemy_style(self.enemy_style)
        require_supported_own_race(self.race)
        if self.enemy_race not in ENEMY_RACES:
            raise ValueError("Unsupported enemy_race")
        if not isinstance(self.map_name, str) or not self.map_name.strip():
            raise ValueError("map_name must be nonempty")
        limit = self.game_time_limit_seconds
        if limit is not None and (type(limit) not in (int, float) or not math.isfinite(limit) or limit <= 0):
            raise ValueError("game_time_limit_seconds must be positive and finite, or None")
        if self.seed is not None and (type(self.seed) is not int or self.seed < 0):
            raise ValueError("seed must be a non-negative integer or None")
        if type(self.blocking_decisions) is not bool or type(self.realtime) is not bool:
            raise ValueError("blocking_decisions and realtime must be booleans")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
