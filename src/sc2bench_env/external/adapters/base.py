"""External Agent adapters. They launch an Agent; they do not play the match."""
from __future__ import annotations


import json
import os
import threading
from pathlib import Path
from typing import Any, Optional, Protocol

from sc2bench_env.external.turn import AgentContext, AgentHandle, TurnInput, TurnRun

# The local VPN listens here. Codex reaches the model through it; the match MCP stays on localhost.
LOCAL_PROXY = "http://127.0.0.1:7000"


class AgentAdapter(Protocol):
    def open(self, context: AgentContext, first_input: TurnInput) -> AgentHandle: ...

    def run_turn(self, handle: AgentHandle, turn_input: TurnInput) -> TurnRun: ...

    def close(self, handle: AgentHandle) -> None: ...


def render_template(argv: list[Any], variables: dict[str, str]) -> list[str]:
    rendered = []
    for part in argv:
        text = str(part)
        for key, value in variables.items():
            text = text.replace("{{" + key + "}}", value)
        rendered.append(text)
    return rendered


def extract_session_id(stream: str, *, event: str, field: str) -> Optional[str]:
    for line in stream.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("type") != event and payload.get("event") != event:
            continue
        value = payload.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def codex_app_server_command(*, mcp_url: str, use_token: bool, reasoning_effort: str) -> list[str]:
    """The CLI command that stays up for one match. Defined with the session."""
    from sc2bench_env.external.adapters.codex_session import codex_app_server_command as command

    return command(mcp_url=mcp_url, use_token=use_token, reasoning_effort=reasoning_effort)


def codex_directories(work_dir: str) -> tuple[Path, Path, Path]:
    """Absolute player, Codex home, and workspace paths.

    Codex resolves CODEX_HOME and its cwd absolutely. The player directory lives
    inside the episode record while the match runs, and is removed afterwards.
    """
    work = Path(work_dir).resolve()
    workspace = work / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return work, work / "codex_home", workspace


def write_codex_config(home: Path, *, mcp_url: str, use_token: bool, tool_timeout: int = 120) -> None:
    home.mkdir(parents=True, exist_ok=True)
    lines = [
        'approval_policy = "never"',
        'sandbox_mode = "read-only"',
        "",
        "[features]",
        "skip_host_skill_discovery = true",
        "",
        "[skills]",
        "include_instructions = false",
        "",
        "[mcp_servers.sc2bench]",
        'url = "{0}"'.format(mcp_url),
        "required = true",
        "startup_timeout_sec = 10",
        "tool_timeout_sec = {0}".format(int(tool_timeout)),
    ]
    if use_token:
        lines.append('bearer_token_env_var = "SC2BENCH_MCP_TOKEN"')
    (home / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


class CodexCliAdapter:
    """One Codex CLI process and one thread for the whole match."""

    adapter_type = "codex_cli"

    def __init__(self, *, grace_seconds: float = 5.0) -> None:
        self.grace_seconds = grace_seconds
        self._transcript = None

    def bind_transcript(self, transcript) -> None:
        """Receive the model's visible text. Reasoning stays out of the match record."""
        self._transcript = transcript

    def open(self, context: AgentContext, first_input: TurnInput) -> AgentHandle:
        from sc2bench_env.external.adapters.codex_session import CodexAppSession

        self._record(context, first_input.render(first=True))
        session = CodexAppSession(
            context, grace_seconds=self.grace_seconds, transcript=self._transcript,
        )
        session.start()
        try:
            thread_id = session.open_thread()
            session.user_turn(first_input.render(first=True))
        except Exception:
            session.close()
            raise
        return AgentHandle(thread_id, self.adapter_type, {"session": session, "context": context})

    def run_turn(self, handle: AgentHandle, turn_input: TurnInput) -> TurnRun:
        session = handle.extras.get("session")
        if session is None:
            raise RuntimeError("missing Codex session on the resume handle")
        message = turn_input.render(first=False)
        self._record(context_from(handle), message)
        session.user_turn(message)
        if session.thread_id != handle.opaque_resume_id:
            raise RuntimeError("Codex CLI started a new thread instead of continuing the match")
        return TurnRun()

    def remind(self, handle: AgentHandle, text: str) -> None:
        """Send a correction on the open decision. The game snapshot stays frozen."""
        session = handle.extras.get("session")
        if session is None:
            raise RuntimeError("missing Codex session on the resume handle")
        self._record(context_from(handle), text)
        session.user_turn(text)

    def close(self, handle: AgentHandle) -> None:
        session = handle.extras.get("session")
        if session is not None:
            session.close()

    def _record(self, context: AgentContext, message: str) -> None:
        work, _home, _workspace = codex_directories(context.work_dir)
        inputs = work / "turn_inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        (inputs / "latest.txt").write_text(message, encoding="utf-8")


def codex_process_env(context: AgentContext, *, codex_home: Optional[str] = None) -> dict[str, str]:
    """Give Codex the local VPN and a clean home. The match MCP stays off that proxy."""
    env = os.environ.copy()
    env.pop("CODEX_HOME", None)
    if codex_home:
        env["CODEX_HOME"] = codex_home
    env.pop("SC2BENCH_MCP_TOKEN", None)
    if context.mcp_token:
        env["SC2BENCH_MCP_TOKEN"] = context.mcp_token
    proxy = env.get("HTTPS_PROXY") or env.get("https_proxy") or LOCAL_PROXY
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env[key] = proxy
    bypass = [item.strip() for item in env.get("NO_PROXY", env.get("no_proxy", "")).split(",") if item.strip()]
    for host in ("127.0.0.1", "localhost"):
        if host not in bypass:
            bypass.append(host)
    env["NO_PROXY"] = ",".join(bypass)
    env["no_proxy"] = env["NO_PROXY"]
    return env


def context_from(handle: AgentHandle) -> AgentContext:
    context = handle.extras.get("context")
    if not isinstance(context, AgentContext):
        raise RuntimeError("missing Agent context on the resume handle")
    return context


def _drain(pipe) -> tuple[threading.Thread, list[str]]:
    chunks: list[str] = []

    def read() -> None:
        try:
            chunks.append(pipe.read() or "")
        except Exception as error:
            chunks.append("[stderr unreadable: {0}]".format(error))

    thread = threading.Thread(target=read, name="sc2bench-agent-stderr", daemon=True)
    thread.start()
    return thread, chunks
