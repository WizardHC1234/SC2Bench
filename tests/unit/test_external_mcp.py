"""External Agents play a fake match through the real MCP server."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from sc2bench_env.external.adapters.base import CodexCliAdapter, _drain, codex_app_server_command, extract_session_id, write_codex_config
from sc2bench_env.external.process import text_pipe_kwargs
from sc2bench_env.external.channel import Command, PlayerChannel
from sc2bench_env.external.config import match_file_from_dict
from sc2bench_env.external.mcp_server import MatchMcpServer
from sc2bench_env.external.runner import execute_command, run_builtin_mcp, run_versus_mcp
from sc2bench_env.external.turn import AgentContext, AgentHandle, TurnRun
from sc2bench_env.env import Environment
from sc2bench_env.interface.config import EpisodeConfig
from sc2bench_env.versus.match import VersusMatch


def _rpc(context: AgentContext, method: str, params: dict, request_id: int = 1) -> dict:
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if context.mcp_token:
        headers["Authorization"] = "Bearer " + context.mcp_token
    request = Request(context.mcp_url, data=body, headers=headers)
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["result"]


class _ScriptedAgent:
    def open(self, context: AgentContext, first_input):
        rendered = first_input.render(first=True)
        assert "You are an autonomous StarCraft II agent" in rendered
        assert "[Current Observation]" in rendered
        assert "continuity-marker" in rendered
        assert '"game_time_seconds"' not in rendered
        self._decide(context)
        return AgentHandle("kept-session", "script", {"context": context})

    def run_turn(self, handle: AgentHandle, turn_input):
        assert "You are an autonomous StarCraft II agent" not in turn_input.render(first=False)
        assert handle.opaque_resume_id == "kept-session"
        self._decide(handle.extras["context"])
        return TurnRun()

    def close(self, handle: AgentHandle) -> None:
        return None

    def _decide(self, context: AgentContext) -> None:
        _rpc(context, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}})
        listed = _rpc(context, "tools/list", {}, request_id=2)
        names = [item["name"] for item in listed["tools"]]
        assert "advance" in names
        assert "query_map_overview" in names
        assert "research" in names
        committed = _rpc(context, "tools/call", {"name": "advance", "arguments": {"seconds": 1}}, request_id=3)
        assert "Status: committed" in committed["content"][0]["text"]
        assert committed["isError"] is False
        closed = _rpc(context, "tools/call", {"name": "advance", "arguments": {"seconds": 1}}, request_id=4)
        assert "turn_closed" in closed["content"][0]["text"]


def test_agent_pipes_keep_utf8_bytes_that_gbk_rejects():
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stderr.buffer.write('决策'.encode('utf-8') + bytes([0xaf]))"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **text_pipe_kwargs(),
    )
    thread, chunks = _drain(process.stderr)
    process.wait(timeout=10)
    thread.join(timeout=2)
    text = "".join(chunks)
    assert "决策" in text
    assert "\ufffd" in text


def test_codex_home_stays_absolute_when_the_work_dir_is_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    captured = {"turns": []}

    class _In:
        def write(self, text):
            payload = json.loads(text)
            method = payload.get("method")
            request_id = payload.get("id")
            if method == "initialize":
                _out.push(json.dumps({"id": request_id, "result": {"userAgent": "test"}}) + "\n")
            elif method == "thread/start":
                _out.push(json.dumps({"id": request_id, "result": {"thread": {"id": "thread-9"}}}) + "\n")
            elif method == "turn/start":
                captured["turns"].append(payload["params"]["input"][0]["text"])
                _out.push(json.dumps({"id": request_id, "result": {"turn": {"id": "turn-1"}}}) + "\n")
                _out.push(json.dumps({"method": "turn/completed", "params": {"threadId": "thread-9"}}) + "\n")
            return len(text)

        def flush(self):
            return None

    class _Out:
        def __init__(self):
            import queue
            self.queue = queue.Queue()

        def push(self, line):
            self.queue.put(line)

        def readline(self):
            return self.queue.get()

    class _Err:
        def readline(self):
            return ""

    class _Process:
        def __init__(self):
            self.stdin = _In()
            self.stdout = _out
            self.stderr = _Err()
            self.pid = 1

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

    _out = _Out()

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        captured["env"] = kwargs["env"]
        return _Process()

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    source = tmp_path / "user_codex"
    source.mkdir()
    (source / "auth.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("sc2bench_env.external.adapters.codex_session.user_codex_home", lambda: source)
    monkeypatch.setattr("sc2bench_env.external.adapters.codex_session.subprocess.Popen", fake_popen)
    context = AgentContext(
        "http://127.0.0.1/mcp/x", "token", "match", "player_0",
        "records/player_0", "gpt-5.6-luna", "low", 0,
    )
    from sc2bench_env.external.turn import TurnInput
    first = TurnInput("turn-a", {"game": {"race": "terran"}}, marker="continuity-marker")
    monkeypatch.setattr(TurnInput, "render", lambda self, *, first: "SYSTEM\n\nOBS" if first else "OBS")
    handle = CodexCliAdapter().open(context, first)
    command = captured["command"]
    assert command[:3] == ["codex", "app-server", "--stdio"]
    home = Path(captured["env"]["CODEX_HOME"])
    assert home.name == "codex_clean"
    assert home.is_absolute()
    assert (home / "auth.json").is_file()
    assert "memories = false" in (home / "config.toml").read_text(encoding="utf-8")
    assert not (home / "sessions").exists()
    assert not (home / "memories").exists()
    assert captured["env"]["HTTPS_PROXY"] == "http://127.0.0.1:7000"
    assert "127.0.0.1" in captured["env"]["NO_PROXY"]
    assert Path(captured["cwd"]).is_absolute()
    assert "features.skip_host_skill_discovery=true" in command
    assert "skills.include_instructions=false" in command
    assert any(part.startswith("mcp_servers.sc2bench.url=") for part in command)
    assert handle.opaque_resume_id == "thread-9"
    assert captured["turns"] == ["SYSTEM\n\nOBS"]
    CodexCliAdapter().run_turn(handle, TurnInput("turn-b", {"game": {"race": "terran"}}))
    assert captured["turns"][1] == "OBS"
    handle.extras["session"].close()


def test_a_stale_tool_call_does_not_reach_the_turn():
    channel = PlayerChannel()
    channel.publish("turn-a", object())
    command = Command("turn-b", "advance", {"seconds": 1})
    execute_command(channel, None, 0, command)  # type: ignore[arg-type]
    assert command.response["data"]["error"]["code"] == "stale_turn"


def test_codex_session_command_points_at_the_match_mcp(tmp_path):
    command = codex_app_server_command(
        reasoning_effort="low",
        mcp_url="http://127.0.0.1:9/mcp/capability",
        use_token=True,
    )
    assert command[:3] == ["codex", "app-server", "--stdio"]
    assert "resume" not in command
    assert any("http://127.0.0.1:9/mcp/capability" in part for part in command)
    write_codex_config(tmp_path / "home", mcp_url="http://127.0.0.1:9/mcp/capability", use_token=True)
    text = (tmp_path / "home" / "config.toml").read_text(encoding="utf-8")
    assert "bearer_token_env_var" in text
    assert "SC2BENCH_MCP_TOKEN" in text
    assert "http://127.0.0.1:9/mcp/capability" in text
    assert "approval_policy = \"never\"" in text
    assert "skip_host_skill_discovery = true" in text
    assert "include_instructions = false" in text
    found = extract_session_id(
        '{"type":"thread.started","thread_id":"thread-7"}\n',
        event="thread.started",
        field="thread_id",
    )
    assert found == "thread-7"


def test_match_file_uses_the_validation_model_and_two_external_agents():
    spec = match_file_from_dict({
        "match": {"backend": "fake", "map": "KairosJunctionLE", "max_decisions": 4, "game_time_limit_seconds": 2},
        "validation": {"model": "gpt-5.6-luna", "reasoning_effort": "low"},
        "players": [
            {"id": "player_0", "agent": {"adapter": "codex_cli"}},
            {"id": "player_1", "agent": {"adapter": "native_mcp_cli", "start_command": ["agent"], "resume_command": ["agent"]}},
        ],
    })
    assert spec.model == "gpt-5.6-luna"
    assert spec.map_name == "KairosJunctionLE"
    assert spec.players[1].adapter == "native_mcp_cli"


def test_two_external_agents_play_a_match_through_mcp(tmp_path):
    channels = (PlayerChannel(), PlayerChannel())
    server = MatchMcpServer(channels)
    server.start()
    try:
        urls = server.urls()
        tokens = server.tokens()
        contexts = [
            AgentContext(urls[index], tokens[index], "match", "player_{0}".format(index), str(tmp_path / str(index)), "gpt-5.6-luna", "low", index)
            for index in range(2)
        ]
        match = VersusMatch(backend="fake", record_dir=tmp_path, record_trajectory=False)
        match.reset(EpisodeConfig(race="terran", enemy_race="zerg", game_time_limit_seconds=2, blocking_decisions=True))
        outcome = run_versus_mcp(
            match,
            (_ScriptedAgent(), _ScriptedAgent()),
            contexts,
            server=server,
            channels=channels,
            max_decisions=6,
            marker="continuity-marker",
        )
    finally:
        server.stop()
    assert outcome["players"][0]["end_reason"] == "time_limit"
    assert outcome["players"][1]["end_reason"] == "time_limit"


def test_one_external_agent_plays_the_builtin_ai(tmp_path):
    class VisibleAgent(_ScriptedAgent):
        def bind_transcript(self, transcript):
            self.transcript = transcript

        def _decide(self, context):
            self.transcript("The current production needs time.", "commentary")
            rejected = _rpc(context, "tools/call", {
                "name": "train", "arguments": {"target": "scv", "count": 0},
            })
            assert rejected["isError"] is True
            super()._decide(context)
            self.transcript("The decision is submitted.", "final_answer")

    spec = match_file_from_dict({
        "match": {
            "backend": "fake",
            "map": "KairosJunctionLE",
            "opponent": "easy",
            "enemy_style": "macro",
            "max_decisions": 6,
            "game_time_limit_seconds": 2,
        },
        "validation": {"model": "gpt-5.6-luna", "reasoning_effort": "low", "marker": "continuity-marker"},
        "players": [
            {"id": "player_0", "agent": {"adapter": "codex_cli"}},
            {"id": "computer", "agent": {"adapter": "builtin"}},
        ],
    })
    assert spec.plays_builtin is True
    assert spec.external_agents[0].adapter == "codex_cli"
    assert spec.opponent == "easy"
    assert spec.enemy_style == "macro"
    channel = PlayerChannel()
    server = MatchMcpServer((channel,))
    server.start()
    try:
        context = AgentContext(
            server.urls()[0], server.tokens()[0], "match", "player_0",
            str(tmp_path / "player"), "gpt-5.6-luna", "low", 0,
        )
        env = Environment(backend="fake", record_dir=tmp_path, record_trajectory=True)
        env.reset(EpisodeConfig(
            race="terran", enemy_race="zerg", opponent="easy", enemy_style="macro",
            game_time_limit_seconds=2, blocking_decisions=True,
        ))
        outcome = run_builtin_mcp(
            env, VisibleAgent(), context,
            server=server, channel=channel, max_decisions=6, marker="continuity-marker",
        )
    finally:
        server.stop()
    assert outcome["mode"] == "builtin"
    assert outcome["opponent"] == "easy"
    assert outcome["end_reason"] == "time_limit"
    assert outcome["result"] == "Result.Tie"
    session = json.loads(next(tmp_path.glob("*/session.json")).read_text(encoding="utf-8"))
    roles = [message["role"] for message in session["messages"]]
    assert roles[0] == "system"
    assert "controlling terran" in session["messages"][0]["content"]
    assert "user" in roles and "tool" in roles
    assert session["model"] == "gpt-5.6-luna"
    assert session["scope"] == "episode"
    log = next(tmp_path.glob("*/log.txt")).read_text(encoding="utf-8")
    for text in (
        "[Agent input]", "[Current Observation]", "[Economy]",
        "[Model note]\nThe current production needs time.",
        '[Tool call]\nadvance {\n  "seconds": 1\n}',
        "[Tool result]\nStatus: committed", "Status: rejected",
        "The decision is submitted.",
    ):
        assert text in log


def test_agent_files_stay_in_the_record_until_the_session_is_saved(tmp_path):
    from sc2bench_env.external.adapters.codex_session import export_codex_session
    from sc2bench_env.paths import match_agent_directory, remove_agent_workspace

    record = tmp_path / "episode"
    record.mkdir()
    work = match_agent_directory(record)
    assert work == record.resolve() / "agent"
    sessions = work / "codex_clean" / "sessions" / "2026" / "10" / "22"
    sessions.mkdir(parents=True)
    (sessions / "rollout-one.jsonl").write_text('{"type":"session_meta"}\n', encoding="utf-8")
    (work / "codex_clean" / "auth.json").write_text("{}\n", encoding="utf-8")
    copied = export_codex_session(str(work), record)
    remove_agent_workspace(str(work), record)
    assert copied == [record / "codex_session" / "2026" / "10" / "22" / "rollout-one.jsonl"]
    assert copied[0].read_text(encoding="utf-8").startswith("{")
    assert not work.exists()
    assert list(record.rglob("auth.json")) == []


def test_recorded_transcript_includes_the_model_reply(capsys):
    from sc2bench_env.external.adapters.codex_session import CodexAppSession
    from sc2bench_env.external.runner import EpisodeDrive

    drive = EpisodeDrive()
    drive.note_assistant("I'll train an SCV.", "commentary")
    drive.add_tool(
        "train", {"unit_type": "scv"},
        {"status": "staged", "data": {}, "decision_boundary": False},
    )
    drive.note_assistant("SCV queued.", "final_answer")
    drive.flush_assistant()
    assert drive.messages[0]["content"] == "I'll train an SCV."
    assert drive.messages[0]["tool_calls"][0]["function"]["name"] == "train"
    assert drive.messages[1]["role"] == "tool"
    assert drive.messages[2] == {"role": "assistant", "content": "SCV queued."}

    notes = []
    session = CodexAppSession.__new__(CodexAppSession)
    session._transcript = lambda text, phase: notes.append((text, phase))
    session._capture_agent_message({
        "method": "item/completed",
        "params": {"item": {"type": "agentMessage", "text": " hello ", "phase": "commentary"}},
    })
    session._capture_agent_message({
        "method": "item/completed",
        "params": {"item": {"type": "reasoning", "encrypted_content": "secret"}},
    })
    assert notes == [("hello", "commentary")]
    log = capsys.readouterr().out
    assert log.count("I'll train an SCV.") == 1
    assert log.count("SCV queued.") == 1
    assert '[Tool call]\ntrain {\n  "unit_type": "scv"\n}' in log
    assert "[Tool result]\nStatus: staged" in log
    assert "secret" not in log


def test_codex_disconnect_after_reply_does_not_report_a_server_failure():
    import http.client
    import socket
    import struct
    import threading
    from urllib.parse import urlsplit

    channel = PlayerChannel()
    server = MatchMcpServer((channel,))
    completed = threading.Event()
    errors = []
    original = server._httpd.process_request_thread

    def finish_request(*args):
        try:
            original(*args)
        finally:
            completed.set()

    server._httpd.process_request_thread = finish_request
    server._httpd.handle_error = lambda *args: errors.append(args[1])
    server.start()
    connection = socket.create_connection(('127.0.0.1', server.port), timeout=3)
    try:
        path = urlsplit(server.urls()[0]).path
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}).encode()
        headers = (f'POST {path} HTTP/1.1\r\nHost: localhost\r\n'
                   f'Authorization: Bearer {server.tokens()[0]}\r\n'
                   f'Content-Length: {len(body)}\r\n\r\n').encode()
        connection.sendall(headers + body)
        response = http.client.HTTPResponse(connection)
        response.begin()
        assert json.loads(response.read())['result'] == {}
        response.close()
        # Codex can terminate its persistent HTTP connection at match end.
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
        connection.close()
        assert completed.wait(3)
        assert not errors
    finally:
        connection.close()
        server.stop()
