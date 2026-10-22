"""Match file for an external-Agent game. The model belongs to the Agent, not the platform."""
from __future__ import annotations


import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from sc2bench_env.interface.opponents import normalize_enemy_style, normalize_opponent


@dataclass
class AgentSpec:
    adapter: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class MatchFile:
    backend: str = "sharpy"
    map_name: str = "KairosJunctionLE"
    race: str = "terran"
    enemy_race: str = "zerg"
    opponent: str = "easy"
    enemy_style: str = "random"
    max_decisions: int = 500
    game_time_limit_seconds: float = 1800
    seed: Optional[int] = None
    model: str = "gpt-5.6-luna"
    reasoning_effort: str = "low"
    players: tuple[AgentSpec, ...] = field(
        default_factory=lambda: (AgentSpec("codex_cli"), AgentSpec("builtin")),
    )
    port: int = 0
    marker: Optional[str] = None

    @property
    def external_agents(self) -> tuple[AgentSpec, ...]:
        return tuple(player for player in self.players if player.adapter != "builtin")

    @property
    def plays_builtin(self) -> bool:
        return any(player.adapter == "builtin" for player in self.players) or len(self.players) == 1


def match_file_from_dict(raw: dict[str, Any]) -> MatchFile:
    if not isinstance(raw, dict):
        raise TypeError("match config must be an object")
    match = raw.get("match") if isinstance(raw.get("match"), dict) else {}
    validation = raw.get("validation") if isinstance(raw.get("validation"), dict) else {}
    players_raw = raw.get("players")
    players: list[AgentSpec] = []
    if isinstance(players_raw, list):
        for item in players_raw:
            if not isinstance(item, dict):
                continue
            agent = item.get("agent") if isinstance(item.get("agent"), dict) else {}
            adapter = str(agent.get("adapter") or "")
            if not adapter:
                raise ValueError("each player needs agent.adapter")
            options = {key: value for key, value in agent.items() if key != "adapter"}
            players.append(AgentSpec(adapter, options))
    _require_player_shape(players)
    limit = match.get("game_time_limit_seconds", 1800)
    decisions = match.get("max_decisions", 500)
    if type(decisions) is not int or decisions < 1:
        raise ValueError("max_decisions must be a positive integer")
    return MatchFile(
        backend=str(match.get("backend") or "sharpy"),
        map_name=str(match.get("map") or "KairosJunctionLE"),
        race=str(match.get("race") or "terran"),
        enemy_race=str(match.get("enemy_race") or "zerg"),
        opponent=normalize_opponent(str(match.get("opponent") or "easy")),
        enemy_style=normalize_enemy_style(str(match.get("enemy_style") or "random")),
        max_decisions=decisions,
        game_time_limit_seconds=float(limit),
        seed=match.get("seed") if isinstance(match.get("seed"), int) else None,
        model=str(validation.get("model") or "gpt-5.6-luna"),
        reasoning_effort=str(validation.get("reasoning_effort") or "low"),
        players=tuple(players),
        port=int(match.get("port") or 0),
        marker=validation.get("marker") if isinstance(validation.get("marker"), str) else None,
    )


def _require_player_shape(players: list[AgentSpec]) -> None:
    builtins = [player for player in players if player.adapter == "builtin"]
    if len(players) == 1 and not builtins:
        return
    if len(players) == 2 and len(builtins) == 1:
        return
    if len(players) == 2 and not builtins:
        return
    raise ValueError(
        "a match config needs one external Agent against the builtin AI, "
        "or two external Agents"
    )


def load_match_file(path: str | Path) -> MatchFile:
    file = Path(path)
    text = file.read_text(encoding="utf-8-sig")
    if file.suffix.lower() == ".json" or text.lstrip().startswith("{"):
        raw = json.loads(text)
    else:
        try:
            import yaml
        except ImportError as error:
            raise ImportError("YAML match configs require PyYAML, or save the file as JSON") from error
        raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError("match config must be an object")
    return match_file_from_dict(raw)
