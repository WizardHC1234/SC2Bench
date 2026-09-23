"""Two agents, one match. Each side waits on its own advance."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from sc2bench_env.benchmark import AgentInput, AgentStopped, AgentTurn
from sc2bench_env.env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.races import require_supported_own_race
from sc2bench_env.paths import resolve_record_dir
from sc2bench_env.recording.trajectory import _episode_folder_name
from sc2bench_env.versus.fake_game import FakeVersusGame


def _player_config(config: EpisodeConfig, *, race: str, enemy_race: str) -> EpisodeConfig:
    return EpisodeConfig(
        race=race,
        enemy_race=enemy_race,
        map_name=config.map_name,
        opponent=config.opponent,
        game_time_limit_seconds=config.game_time_limit_seconds,
        seed=config.seed,
        blocking_decisions=True,
        enemy_style=config.enemy_style,
    )


class VersusMatch:
    """Shared game. ``wait_next`` returns whichever side is paused."""

    def __init__(
        self, *, backend: str = "sharpy",
        record_trajectory: bool = True,
        record_dir: Optional[str | Path] = None,
    ) -> None:
        if backend == "fake":
            self._game = FakeVersusGame()
        elif backend == "sharpy":
            from sc2bench_env.backends.sharpy.versus import SharpyVersusGame
            self._game = SharpyVersusGame()
        else:
            raise ValueError("versus backend must be 'fake' or 'sharpy'")
        self._record_trajectory = record_trajectory
        self._record_root = resolve_record_dir(record_dir)
        self._match_dir: Optional[Path] = None
        self._sides: list[Environment] = []
        self._cursor = 0
        self._closed_match = False
        self._outcome: Optional[dict[str, Any]] = None

    def reset(self, config: EpisodeConfig) -> tuple[Any, Any]:
        if not isinstance(config, EpisodeConfig):
            raise TypeError("reset requires an EpisodeConfig")
        require_supported_own_race(config.race)
        require_supported_own_race(config.enemy_race)
        if not config.blocking_decisions:
            raise ValueError("versus matches require blocking decisions")
        if self._sides:
            self.close()
            self._closed_match = False
            self._outcome = None
        self._match_dir = self._allocate_dir(config)
        replay = self._match_dir / "replay.SC2Replay" if self._record_trajectory else None
        self._game.set_replay_path(replay)
        snapshots = self._game.start(config)
        configs = (
            _player_config(config, race=config.race, enemy_race=config.enemy_race),
            _player_config(config, race=config.enemy_race, enemy_race=config.race),
        )
        observations = []
        self._sides = []
        for index, (backend, player_config, snapshot) in enumerate(
            zip(self._game.backends, configs, snapshots)
        ):
            environment = Environment(
                backend, record_trajectory=self._record_trajectory, record_dir=self._match_dir,
            )
            observations.append(environment.open_player(
                player_config, snapshot, folder_name=f"player_{index}",
            ))
            self._sides.append(environment)
        self._cursor = 0
        return observations[0], observations[1]

    def waiting(self) -> list[int]:
        return list(self._game.waiting())

    def wait_next(self) -> Optional[int]:
        """Block until one side needs a decision. None when the match is over."""
        while True:
            waiting = self._game.waiting()
            if waiting:
                return self._choose(waiting)
            if self._game.finished():
                return None
            error = self._game.failure()
            if error is not None:
                raise RuntimeError(f"versus game failed: {error}") from error
            self._game.wait_for_change(1.0)

    def request(self, player: int) -> AgentInput:
        self.collect(player)
        environment = self._side(player)
        if environment.latest_observation is None:
            raise RuntimeError("player has no observation")
        return AgentInput(
            environment.latest_observation, environment.latest_feedback,
            environment.get_context(), tool_schemas=tuple(environment.tool_schemas()),
            call_tool=environment.call_tool,
        )

    def collect(self, player: int) -> None:
        environment = self._side(player)
        if environment._pending_release is None:
            return
        snapshot = environment.backend.snapshot()
        if player in self._game.waiting() or snapshot.terminated or self._game.finished():
            environment.complete_pending()

    def submit(self, player: int, decision, *, agent_context: Optional[dict[str, Any]] = None) -> bool:
        """Release this side. Return True when the decision was rejected and the side stayed paused."""
        if player not in self._game.waiting():
            raise RuntimeError(f"player {player} is not waiting for a decision")
        self.collect(player)
        return self._side(player).release_decision(decision, agent_context=agent_context)

    def outcome(self) -> dict[str, Any]:
        if self._outcome is not None:
            return self._outcome
        return self._read_outcome()

    def close(self, *, end_reason: str = "closed_by_caller") -> None:
        if self._closed_match:
            return
        self._game.close(end_reason=end_reason)
        for player in range(len(self._sides)):
            if self._sides[player]._pending_release is not None:
                self._sides[player].complete_pending()
        for side in self._sides:
            if not side._closed:
                side.close(end_reason=end_reason)
        self._outcome = self._read_outcome()
        self._closed_match = True

    def _side(self, player: int) -> Environment:
        if player not in (0, 1) or player >= len(self._sides):
            raise IndexError("versus player must be 0 or 1")
        return self._sides[player]

    def _choose(self, waiting: list[int]) -> int:
        for offset in range(2):
            player = (self._cursor + offset) % 2
            if player in waiting:
                self._cursor = (player + 1) % 2
                return player
        return waiting[0]

    def _allocate_dir(self, config: EpisodeConfig) -> Path:
        root = self._record_root
        root.mkdir(parents=True, exist_ok=True)
        name = _episode_folder_name({**config.to_dict(), "opponent": "agent"})
        suffix = 1
        while True:
            directory = root / (name if suffix == 1 else f"{name}_{suffix}")
            try:
                directory.mkdir()
            except FileExistsError:
                suffix += 1
                continue
            return directory

    def _read_outcome(self) -> dict[str, Any]:
        players = []
        for index, side in enumerate(self._sides):
            summary = side.recorder.summary if side.recorder is not None else None
            snapshot = None
            try:
                snapshot = side.backend.snapshot()
            except Exception:
                snapshot = None
            result = None if summary is None else summary.get("result")
            reason = None if summary is None else summary.get("end_reason")
            if reason is None and snapshot is not None and getattr(snapshot, "terminated", False):
                result = getattr(snapshot, "result", None)
                reason = side._episode_end_reason(snapshot)
            players.append({
                "player": index,
                "result": result,
                "end_reason": reason,
                "record_dir": None if side.record_path is None else str(side.record_path),
            })
        return {
            "record_dir": None if self._match_dir is None else str(self._match_dir),
            "players": players,
        }


def run_versus(
    match: VersusMatch,
    agent: Any,
    opponent: Any,
    config: EpisodeConfig,
    *,
    max_decisions: int = 500,
    verbose: bool = True,
) -> dict[str, Any]:
    """Ask whichever side is paused. The two agents are independent.

    ``agent`` plays the configured race and ``opponent`` plays ``enemy_race``.
    They do not have to be the same class or share a model. Each is called with
    an ``AgentInput`` and may return an ``AgentTurn`` or a decision batch.
    ``max_decisions`` is counted per side.
    """
    agents = (agent, opponent)
    if type(max_decisions) is not int or max_decisions < 1:
        raise ValueError("max_decisions must be a positive integer")
    match.reset(config)
    calls = [0, 0]
    try:
        while True:
            player = match.wait_next()
            if player is None:
                break
            if calls[player] >= max_decisions:
                match.close(end_reason="decision_limit")
                break
            request = match.request(player)
            if verbose:
                label = getattr(agents[player], "__name__", None) or type(agents[player]).__name__
                print(
                    f"player={player} agent={label} round={calls[player] + 1} "
                    f"game_seconds={request.observation.game.game_time_seconds:.1f}",
                    flush=True,
                )
            try:
                turn = agents[player](request)
            except AgentStopped as stop:
                match.close(end_reason=stop.end_reason)
                break
            if not isinstance(turn, AgentTurn):
                turn = AgentTurn(decision=turn)
            for failure in turn.call_failures:
                info = match._side(player).record_agent_call_failure(failure)
                if info["terminated"]:
                    break
            else:
                info = None
            if info and info["terminated"]:
                break
            if turn.stop_after_call_failures or turn.decision is None:
                match.close(end_reason="agent_call_failed")
                break
            match.submit(player, turn.decision, agent_context=turn.agent_context)
            calls[player] += 1
    finally:
        match.close()
    return match.outcome()
