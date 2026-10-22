"""One Codex CLI process for a whole match.

The match exposes MCP. This process connects to it once, opens one thread, and
receives each later decision as a new user message in that same thread.
"""
from __future__ import annotations


import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

from sc2bench_env.external.adapters.base import codex_directories, codex_process_env
from sc2bench_env.external.process import stop_process, text_pipe_kwargs
from sc2bench_env.external.turn import AgentContext


def user_codex_home() -> Path:
    return Path.home() / ".codex"


def prepare_clean_codex_home(work_dir: str) -> Path:
    """A match-local Codex home: the user's login, and no other conversations or memory."""
    home = Path(work_dir).resolve() / "codex_clean"
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True)
    auth = user_codex_home() / "auth.json"
    if not auth.is_file():
        raise RuntimeError("Codex login was not found. Sign in with Codex once, then run the match again.")
    shutil.copy2(auth, home / "auth.json")
    (home / "config.toml").write_text(
        "\n".join([
            "suppress_unstable_features_warning = true",
            "",
            "[features]",
            "memories = false",
            "skip_host_skill_discovery = true",
            "",
            "[skills]",
            "include_instructions = false",
            "",
        ]) + "\n",
        encoding="utf-8",
    )
    return home


def export_codex_session(work_dir: str, record_dir: Path) -> list[Path]:
    """Copy this match's Codex conversation into the episode record.

    The login file and memory databases stay in the agent home.
    """
    sessions = Path(work_dir).resolve() / "codex_clean" / "sessions"
    if not sessions.is_dir():
        return []
    written: list[Path] = []
    destination_root = Path(record_dir) / "codex_session"
    for source in sessions.rglob("*.jsonl"):
        if not source.is_file():
            continue
        destination = destination_root / source.relative_to(sessions)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        written.append(destination)
    return written


def codex_app_server_command(*, mcp_url: str, use_token: bool, reasoning_effort: str) -> list[str]:
    """Start Codex once. The MCP server is the match that is already listening."""
    command = [
        "codex", "app-server", "--stdio",
        "--disable", "memories",
        "-c", 'model_reasoning_effort="{0}"'.format(reasoning_effort),
        "-c", 'mcp_servers.sc2bench.default_tools_approval_mode="approve"',
        "-c", "features.skip_host_skill_discovery=true",
        "-c", "skills.include_instructions=false",
        "-c", 'mcp_servers.sc2bench.url="{0}"'.format(mcp_url),
        "-c", "mcp_servers.sc2bench.required=true",
        "-c", "mcp_servers.sc2bench.startup_timeout_sec=10",
        "-c", "mcp_servers.sc2bench.tool_timeout_sec=120",
    ]
    if use_token:
        command.append("-c")
        command.append('mcp_servers.sc2bench.bearer_token_env_var="SC2BENCH_MCP_TOKEN"')
    return command


class CodexAppSession:
    """Stdio session: initialize, one thread, then one user message per decision."""

    def __init__(self, context: AgentContext, *, grace_seconds: float = 5.0, transcript=None) -> None:
        self.context = context
        self.grace_seconds = grace_seconds
        self._transcript = transcript
        self.thread_id = ""
        self.process: Optional[subprocess.Popen] = None
        self._next_id = 1
        self._lock = threading.Lock()
        self._pending: dict[int, tuple[threading.Event, dict[str, Any]]] = {}
        self._turn_done: Optional[threading.Event] = None
        self._turn_error = ""
        self._reader: Optional[threading.Thread] = None
        self._events_path: Optional[Path] = None

    def start(self) -> None:
        work, _home, workspace = codex_directories(self.context.work_dir)
        self._events_path = work / "events.jsonl"
        self._events_path.write_text("", encoding="utf-8")
        command = codex_app_server_command(
            mcp_url=self.context.mcp_url,
            use_token=bool(self.context.mcp_token),
            reasoning_effort=self.context.reasoning_effort,
        )
        home = prepare_clean_codex_home(self.context.work_dir)
        self.process = subprocess.Popen(
            command,
            cwd=str(workspace),
            env=codex_process_env(self.context, codex_home=str(home)),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **text_pipe_kwargs(),
        )
        assert self.process.stdout is not None and self.process.stderr is not None
        self._reader = threading.Thread(target=self._read_stdout, name="sc2bench-codex-session", daemon=True)
        self._reader.start()
        threading.Thread(target=self._read_stderr, name="sc2bench-codex-stderr", daemon=True).start()
        self._request("initialize", {
            "clientInfo": {"name": "sc2bench", "title": "SC2Bench", "version": "0.1"},
        })
        self._notify("initialized", {})

    def open_thread(self) -> str:
        work, _home, workspace = codex_directories(self.context.work_dir)
        response = self._request("thread/start", {
            "model": self.context.model,
            "cwd": str(workspace),
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
            "ephemeral": False,
        })
        thread = response.get("result", {}).get("thread", {})
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise RuntimeError("Codex CLI did not open a thread")
        self.thread_id = thread_id
        _ = work
        return thread_id

    def user_turn(self, text: str) -> None:
        """Send one observation and wait until Codex finishes that turn."""
        done = threading.Event()
        with self._lock:
            self._turn_done = done
            self._turn_error = ""
        self._request("turn/start", {
            "threadId": self.thread_id,
            "input": [{"type": "text", "text": text}],
            "effort": self.context.reasoning_effort,
        })
        while not done.wait(timeout=0.5):
            if self.process is None or self.process.poll() is not None:
                raise RuntimeError(self._turn_error or "Codex CLI exited before the turn finished")
        if self._turn_error:
            raise RuntimeError(self._turn_error)

    def close(self) -> None:
        stop_process(self.process, grace_seconds=self.grace_seconds)
        self.process = None

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        event = threading.Event()
        box: dict[str, Any] = {}
        with self._lock:
            self._pending[request_id] = (event, box)
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while not event.wait(timeout=0.5):
            if self.process is None or self.process.poll() is not None:
                raise RuntimeError("Codex CLI exited during {0}".format(method))
        if "error" in box:
            message = box["error"].get("message") if isinstance(box["error"], dict) else box["error"]
            raise RuntimeError("Codex CLI {0} failed: {1}".format(method, message))
        return box

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _send(self, payload: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("Codex CLI is not running")
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        while True:
            line = self.process.stdout.readline()
            if not line:
                break
            self._remember(line)
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            request_id = payload.get("id")
            if isinstance(request_id, int):
                with self._lock:
                    pending = self._pending.pop(request_id, None)
                if pending is not None:
                    event, box = pending
                    box.update(payload)
                    event.set()
                continue
            method = payload.get("method")
            if method == "item/completed":
                self._capture_agent_message(payload)
            if method == "turn/completed":
                with self._lock:
                    done = self._turn_done
                if done is not None:
                    done.set()
            elif method == "error":
                params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
                message = str(params.get("message") or params.get("error") or "Codex error")
                with self._lock:
                    self._turn_error = message
                    done = self._turn_done
                if done is not None:
                    done.set()
        with self._lock:
            pending = list(self._pending.values())
            done = self._turn_done
            self._turn_error = self._turn_error or "Codex CLI closed its output"
        for event, box in pending:
            box["error"] = {"message": self._turn_error}
            event.set()
        if done is not None:
            done.set()

    def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        chunks: list[str] = []
        while True:
            line = self.process.stderr.readline()
            if not line:
                break
            chunks.append(line)
        work, _home, _workspace = codex_directories(self.context.work_dir)
        (work / "stderr.txt").write_text("".join(chunks), encoding="utf-8")

    def _capture_agent_message(self, payload: dict[str, Any]) -> None:
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        if item.get("type") != "agentMessage" or self._transcript is None:
            return
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            return
        phase = item.get("phase") if isinstance(item.get("phase"), str) else "commentary"
        self._transcript(text.strip(), phase)

    def _remember(self, line: str) -> None:
        if self._events_path is None:
            return
        with self._events_path.open("a", encoding="utf-8") as handle:
            handle.write(line if line.endswith("\n") else line + "\n")
