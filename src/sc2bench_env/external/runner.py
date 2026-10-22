"""Drive one match. The match thread executes tools while the external Agent is running."""
from __future__ import annotations


import json
import threading
import uuid
from pathlib import Path
from typing import Any, Optional, Sequence

from sc2bench_env.external.adapters.codex_session import export_codex_session
from sc2bench_env.external.channel import Command, PlayerChannel, _error
from sc2bench_env.external.config import MatchFile, load_match_file
from sc2bench_env.external.mcp_server import MatchMcpServer
from sc2bench_env.external.turn import AgentContext, TurnInput
from sc2bench_env.env import Environment
from sc2bench_env.paths import match_agent_directory, remove_agent_workspace
from sc2bench_env.interface.agent import AgentInput
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.interface.observations import agent_observation_view
from sc2bench_env.interface.tools import ToolCall
from sc2bench_env.runtime.tool_turn import ToolTurnError
from sc2bench_env.versus.match import VersusMatch


def execute_command(channel: PlayerChannel, match: VersusMatch, player: int, command: Command) -> None:
    with channel._lock:
        if channel.match_closed or command.turn_id != channel.active_turn_id:
            command.respond(_error("stale_turn", "This tool call belongs to a decision that is no longer active"))
            return
        if channel.closed_turn or channel.active_input is None:
            command.respond(_error("turn_closed", "This decision was already submitted"))
            return
        agent_input = channel.active_input
    result = agent_input.call_tool(ToolCall(command.name, dict(command.arguments)))
    if not result.decision_boundary:
        command.respond(result.to_dict())
        return
    try:
        batch = match.finish_turn(player)
        match.submit(player, batch)
    except (ToolTurnError, RuntimeError) as error:
        channel.close_turn()
        command.respond(_error("turn_closed", str(error)))
        return
    channel.mark_committed()
    channel.close_turn()
    command.respond({
        "status": "committed",
        "turn_id": command.turn_id,
        "decision_boundary": True,
    })


class EpisodeDrive:
    """The decision the builtin-AI match will apply after the Agent returns."""

    def __init__(self) -> None:
        self.turn: Any = None
        self.batch: Any = None
        self.terminated = False
        self.info: dict[str, Any] = {}
        self.end_reason: Optional[str] = None
        self.error: Optional[str] = None
        self.model: Optional[str] = None
        self.messages: list[dict[str, Any]] = []

    def agent_context(self) -> dict[str, Any]:
        return {
            "messages": self.messages,
            "model": self.model,
            "session_scope": "episode",
        }

    def add_observation(self, turn_input: TurnInput, model: str) -> None:
        """The recorded dialogue matches the in-process agent: system once, then each observation."""
        self.model = model
        if not self.messages:
            from sc2bench_env.adapters.llm.prompt import system_prompt

            game = turn_input.observation.get("game") if isinstance(turn_input.observation, dict) else None
            race = str(game.get("race")) if isinstance(game, dict) and game.get("race") else "terran"
            self.messages.append({"role": "system", "content": system_prompt(race=race)})
        self.messages.append({"role": "user", "content": turn_input.render(first=False)})

    def add_tool(self, name: str, arguments: dict[str, Any], payload: dict[str, Any]) -> None:
        from sc2bench_env.external.mcp_server import _agent_visible_text

        call_id = "call_{0}".format(len(self.messages))
        self.messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }],
        })
        self.messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": _agent_visible_text(name, payload),
        })


def execute_environment_command(channel: PlayerChannel, drive: EpisodeDrive, command: Command) -> None:
    def reply(payload: dict[str, Any]) -> None:
        drive.add_tool(command.name, dict(command.arguments), payload)
        command.respond(payload)

    with channel._lock:
        if channel.match_closed or command.turn_id != channel.active_turn_id:
            reply(_error("stale_turn", "This tool call belongs to a decision that is no longer active"))
            return
        if channel.closed_turn or channel.active_input is None:
            reply(_error("turn_closed", "This decision was already submitted"))
            return
        agent_input = channel.active_input
    result = agent_input.call_tool(ToolCall(command.name, dict(command.arguments)))
    if not result.decision_boundary:
        reply(result.to_dict())
        return
    turn = drive.turn
    if turn is None:
        channel.close_turn()
        reply(_error("turn_closed", "This decision was already submitted"))
        return
    try:
        drive.batch = turn.finish()
    except (ToolTurnError, RuntimeError) as error:
        channel.close_turn()
        reply(_error("turn_closed", str(error)))
        return
    channel.mark_committed()
    channel.close_turn()
    reply({
        "status": "committed",
        "turn_id": command.turn_id,
        "decision_boundary": True,
    })


def _drive(function, channel: PlayerChannel, execute) -> Any:
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["value"] = function()
        except BaseException as error:
            box["error"] = error

    thread = threading.Thread(target=work, name="sc2bench-external-agent")
    thread.start()
    while thread.is_alive():
        command = channel.take(0.05)
        if command is not None:
            execute(command)
    thread.join()
    while True:
        command = channel.take(0)
        if command is None:
            break
        execute(command)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def run_versus_mcp(
    match: VersusMatch,
    adapters: Sequence[Any],
    contexts: Sequence[AgentContext],
    *,
    server: MatchMcpServer,
    channels: Sequence[PlayerChannel],
    max_decisions: int = 500,
    marker: Optional[str] = None,
) -> dict[str, Any]:
    """Serve whichever player is paused. One player never has two turns at once."""
    if len(adapters) != 2 or len(contexts) != 2 or len(channels) != 2:
        raise ValueError("a match has two external Agents")
    handles: list[Any] = [None, None]
    calls = [0, 0]
    try:
        while True:
            player = match.wait_next()
            if player is None:
                break
            if calls[player] >= max_decisions:
                match.close(end_reason="decision_limit")
                break
            agent_input = match.request(player)
            turn_id = uuid.uuid4().hex
            channels[player].publish(turn_id, agent_input)
            server.publish_specs(player, tuple(agent_input.tool_specs))
            turn_input = TurnInput(
                turn_id,
                agent_input.observation.to_dict(),
                None if agent_input.feedback is None else agent_input.feedback.to_dict(),
                marker if calls[player] == 0 else None,
                previous=getattr(agent_input.observation, "text_previous", None),
            )
            adapter = adapters[player]
            try:
                if handles[player] is None:
                    handles[player] = _drive(
                        lambda adapter=adapter, context=contexts[player], turn_input=turn_input: adapter.open(context, turn_input),
                        channels[player],
                        lambda command, channel=channels[player], player=player: execute_command(channel, match, player, command),
                    )
                else:
                    handle = handles[player]
                    _drive(
                        lambda adapter=adapter, handle=handle, turn_input=turn_input: adapter.run_turn(handle, turn_input),
                        channels[player],
                        lambda command, channel=channels[player], player=player: execute_command(channel, match, player, command),
                    )
            except Exception:
                match.record_protocol_error(player, "agent_protocol_error")
                match.close(end_reason="agent_protocol_error")
                break
            if not channels[player].committed:
                match.record_protocol_error(player, "missing_advance")
                match.close(end_reason="agent_protocol_error")
                break
            calls[player] += 1
    finally:
        for index, handle in enumerate(handles):
            if handle is not None:
                try:
                    adapters[index].close(handle)
                except Exception:
                    pass
        match.close()
    outcome = match.outcome()
    _attach_codex_sessions(contexts, outcome)
    return outcome


def run_builtin_mcp(
    env: Environment,
    adapter: Any,
    context: AgentContext,
    *,
    server: MatchMcpServer,
    channel: PlayerChannel,
    max_decisions: int = 500,
    marker: Optional[str] = None,
) -> dict[str, Any]:
    """One external Agent against the builtin computer. The computer plays during step."""
    handle = None
    calls = 0
    drive = EpisodeDrive()
    observation = env.latest_observation
    feedback = None
    try:
        while True:
            if drive.terminated or env._closed:
                break
            if calls >= max_decisions:
                drive.end_reason = "decision_limit"
                env.close(end_reason="decision_limit")
                break
            drive.turn = env.begin_tool_turn()
            drive.batch = None
            agent_input = AgentInput(
                agent_observation_view(observation),
                feedback,
                tuple(env.tool_specs()),
                drive.turn.call,
            )
            turn_id = uuid.uuid4().hex
            channel.publish(turn_id, agent_input)
            server.publish_specs(0, tuple(agent_input.tool_specs))
            turn_input = TurnInput(
                turn_id,
                agent_input.observation.to_dict(),
                None if feedback is None else feedback.to_dict(),
                marker if calls == 0 else None,
                previous=getattr(agent_input.observation, "text_previous", None),
            )
            drive.add_observation(turn_input, context.model)
            try:
                if handle is None:
                    handle = _drive(
                        lambda adapter=adapter, context=context, turn_input=turn_input: adapter.open(context, turn_input),
                        channel,
                        lambda command, channel=channel, drive=drive: execute_environment_command(channel, drive, command),
                    )
                else:
                    _drive(
                        lambda adapter=adapter, handle=handle, turn_input=turn_input: adapter.run_turn(handle, turn_input),
                        channel,
                        lambda command, channel=channel, drive=drive: execute_environment_command(channel, drive, command),
                    )
            except Exception as error:
                drive.end_reason = "agent_protocol_error"
                drive.error = "{0}: {1}".format(type(error).__name__, error)
                env.record_protocol_error("agent_protocol_error", agent_context=drive.agent_context())
                env.close(end_reason="agent_protocol_error")
                break
            if not channel.committed or drive.batch is None:
                drive.end_reason = "agent_protocol_error"
                env.record_protocol_error("missing_advance", agent_context=drive.agent_context())
                env.close(end_reason="agent_protocol_error")
                break
            observation, feedback, terminated, info = env.step(
                drive.batch, agent_context=drive.agent_context(),
            )
            drive.terminated = bool(terminated)
            drive.info = dict(info)
            calls += 1
            if drive.terminated:
                drive.end_reason = info.get("end_reason")
                break
    finally:
        if handle is not None:
            try:
                adapter.close(handle)
            except Exception:
                pass
        record = env.record_path
        copied = export_codex_session(context.work_dir, record) if record is not None else []
        remove_agent_workspace(context.work_dir, record)
        snapshot = env.backend.snapshot()
        outcome = {
            "mode": "builtin",
            "opponent": env.config.opponent if env.config is not None else None,
            "enemy_style": env.config.enemy_style if env.config is not None else None,
            "result": drive.info.get("result", snapshot.result),
            "end_reason": drive.end_reason or drive.info.get("end_reason"),
            "error": drive.error,
            "record_dir": None if record is None else str(record),
            "codex_session": [str(path) for path in copied],
        }
        env.close()
    return outcome


def _attach_codex_sessions(contexts: Sequence[AgentContext], outcome: dict[str, Any]) -> None:
    players = outcome.get("players")
    saved: list[str] = []
    if isinstance(players, list):
        for index, player in enumerate(players):
            if not isinstance(player, dict) or index >= len(contexts):
                continue
            record = player.get("record_dir")
            copied = export_codex_session(contexts[index].work_dir, Path(record)) if isinstance(record, str) else []
            player["codex_session"] = [str(path) for path in copied]
            saved.extend(player["codex_session"])
            if isinstance(record, str):
                remove_agent_workspace(contexts[index].work_dir, record)
    outcome["codex_session"] = saved


def build_adapter(adapter: str, options: dict[str, Any]) -> Any:
    from sc2bench_env.external.adapters import CodexCliAdapter, CodexSdkAdapter, NativeMcpCliAdapter

    if adapter == "codex_cli":
        return CodexCliAdapter()
    if adapter == "codex_sdk":
        python = options.get("python")
        return CodexSdkAdapter(python=python if isinstance(python, str) else None)
    if adapter == "native_mcp_cli":
        return NativeMcpCliAdapter(options)
    raise ValueError("unknown external adapter {0}".format(adapter))


def run_config_file(path: str | Path, *, record_dir: Optional[str | Path] = None) -> dict[str, Any]:
    spec = load_match_file(path)
    return run_match_file(spec, record_dir=record_dir)


def run_match_file(spec: MatchFile, *, record_dir: Optional[str | Path] = None) -> dict[str, Any]:
    root = Path(record_dir).resolve() if record_dir is not None else Path("records").resolve()
    root.mkdir(parents=True, exist_ok=True)
    if spec.plays_builtin:
        return _run_builtin_file(spec, root)
    channels = (PlayerChannel(), PlayerChannel())
    server = MatchMcpServer(channels, port=spec.port)
    server.start()
    match_id = uuid.uuid4().hex
    urls = server.urls()
    tokens = server.tokens()
    contexts = []
    for index in range(2):
        contexts.append(AgentContext(
            mcp_url=urls[index],
            mcp_token=tokens[index],
            match_id=match_id,
            player_id="player_{0}".format(index),
            work_dir="",
            model=spec.model,
            reasoning_effort=spec.reasoning_effort,
            player_index=index,
        ))
    adapters = [build_adapter(item.adapter, item.options) for item in spec.players]
    match = VersusMatch(backend=spec.backend, record_dir=root)
    try:
        match.reset(EpisodeConfig(
            race=spec.race,
            enemy_race=spec.enemy_race,
            map_name=spec.map_name,
            game_time_limit_seconds=spec.game_time_limit_seconds,
            seed=spec.seed,
            blocking_decisions=True,
        ))
        for context, side in zip(contexts, match._sides):
            record = side.record_path
            if record is None:
                raise RuntimeError("the match has no record directory for {0}".format(context.player_id))
            context.work_dir = str(match_agent_directory(record))
        return run_versus_mcp(
            match, adapters, contexts,
            server=server, channels=channels,
            max_decisions=spec.max_decisions, marker=spec.marker,
        )
    finally:
        server.stop()


def _run_builtin_file(spec: MatchFile, root: Path) -> dict[str, Any]:
    agent = spec.external_agents[0]
    channel = PlayerChannel()
    server = MatchMcpServer((channel,), port=spec.port)
    server.start()
    match_id = uuid.uuid4().hex
    context = AgentContext(
        mcp_url=server.urls()[0],
        mcp_token=server.tokens()[0],
        match_id=match_id,
        player_id="player_0",
        work_dir="",
        model=spec.model,
        reasoning_effort=spec.reasoning_effort,
        player_index=0,
    )
    env = Environment(backend=spec.backend, record_dir=root)
    try:
        env.reset(EpisodeConfig(
            race=spec.race,
            enemy_race=spec.enemy_race,
            map_name=spec.map_name,
            opponent=spec.opponent,
            enemy_style=spec.enemy_style,
            game_time_limit_seconds=spec.game_time_limit_seconds,
            seed=spec.seed,
            blocking_decisions=True,
        ))
        if env.record_path is None:
            raise RuntimeError("the match has no record directory")
        context.work_dir = str(match_agent_directory(env.record_path))
        return run_builtin_mcp(
            env, build_adapter(agent.adapter, agent.options), context,
            server=server, channel=channel,
            max_decisions=spec.max_decisions, marker=spec.marker,
        )
    finally:
        server.stop()


def concurrent_match_worker(config_path: str, record_dir: str, index: int, queue: Any) -> None:
    """One match process. A second game must not share this process's client."""
    try:
        outcome = run_config_file(config_path, record_dir=record_dir or None)
        outcome["index"] = index
    except Exception as error:
        outcome = {
            "index": index,
            "error": "{0}: {1}".format(type(error).__name__, error),
            "end_reason": "worker_failed",
        }
    queue.put(outcome)


def run_concurrent_matches(
    path: str | Path,
    count: int,
    *,
    record_dir: Optional[str | Path] = None,
) -> list[dict[str, Any]]:
    """Run several copies of one match config at the same time, one process each."""
    if type(count) is not int or count < 1:
        raise ValueError("count must be a positive integer")
    if count == 1:
        outcome = run_config_file(path, record_dir=record_dir)
        outcome["index"] = 0
        return [outcome]
    import multiprocessing as mp
    import queue as queue_module

    ctx = mp.get_context("spawn")
    results = ctx.Queue()
    child_record = "" if record_dir is None else str(Path(record_dir).resolve())
    processes = []
    for index in range(count):
        process = ctx.Process(
            target=concurrent_match_worker,
            args=(str(path), child_record, index, results),
            name="sc2bench-match-{0}".format(index),
        )
        process.start()
        processes.append(process)
    outcomes: list[dict[str, Any]] = []
    while len(outcomes) < count:
        try:
            outcomes.append(results.get(timeout=5))
        except queue_module.Empty:
            if all(process.exitcode is not None for process in processes):
                break
    for process in processes:
        process.join()
    found = {item.get("index") for item in outcomes}
    for index, process in enumerate(processes):
        if index in found:
            continue
        outcomes.append({
            "index": index,
            "error": "match process exited without a result",
            "end_reason": "worker_failed",
            "exit_code": process.exitcode,
        })
    outcomes.sort(key=lambda item: int(item.get("index") or 0))
    return outcomes
