"""Launch a configured external CLI and resume the session id it prints."""
from __future__ import annotations


import os
import subprocess
from pathlib import Path
from typing import Any, Optional

from sc2bench_env.external.adapters.base import extract_session_id, render_template
from sc2bench_env.external.process import stop_process, text_pipe_kwargs
from sc2bench_env.external.turn import AgentContext, AgentHandle, TurnInput, TurnRun


class NativeMcpCliAdapter:
    adapter_type = "native_mcp_cli"

    def __init__(self, spec: dict[str, Any], *, grace_seconds: float = 5.0) -> None:
        self.start_command = list(spec.get("start_command") or [])
        self.resume_command = list(spec.get("resume_command") or [])
        session = spec.get("session_id") if isinstance(spec.get("session_id"), dict) else {}
        self.session_event = str(session.get("event") or "thread.started")
        self.session_field = str(session.get("field") or "thread_id")
        self.grace_seconds = grace_seconds
        if not self.start_command or not self.resume_command:
            raise ValueError("native_mcp_cli needs start_command and resume_command")

    def open(self, context: AgentContext, first_input: TurnInput) -> AgentHandle:
        message = first_input.render(first=True)
        output = self._run(context, self.start_command, message, session_id=None)
        session_id = extract_session_id(output, event=self.session_event, field=self.session_field)
        if not session_id:
            raise RuntimeError("external CLI did not return a session id")
        return AgentHandle(session_id, self.adapter_type, {"context": context})

    def run_turn(self, handle: AgentHandle, turn_input: TurnInput) -> TurnRun:
        context = handle.extras["context"]
        self._run(context, self.resume_command, turn_input.render(first=False), session_id=handle.opaque_resume_id)
        return TurnRun()

    def close(self, handle: AgentHandle) -> None:
        stop_process(handle.extras.get("process"), grace_seconds=self.grace_seconds)

    def _variables(self, context: AgentContext, session_id: Optional[str]) -> dict[str, str]:
        work = Path(context.work_dir)
        work.mkdir(parents=True, exist_ok=True)
        message_file = work / "message.txt"
        return {
            "mcp_url": context.mcp_url,
            "mcp_token_env": "SC2BENCH_MCP_TOKEN",
            "match_id": context.match_id,
            "player_id": context.player_id,
            "work_dir": context.work_dir,
            "config_file": str(work / "agent_config.json"),
            "message_file": str(message_file),
            "session_id": session_id or "",
            "model": context.model,
            "reasoning_effort": context.reasoning_effort,
        }

    def _run(self, context: AgentContext, argv: list[Any], message: str, *, session_id: Optional[str]) -> str:
        variables = self._variables(context, session_id)
        message_path = Path(variables["message_file"])
        message_path.write_text(message, encoding="utf-8")
        env = os.environ.copy()
        env.pop("SC2BENCH_MCP_TOKEN", None)
        if context.mcp_token:
            env["SC2BENCH_MCP_TOKEN"] = context.mcp_token
        process = subprocess.Popen(
            render_template(argv, variables),
            cwd=context.work_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **text_pipe_kwargs(),
        )
        output, _ = process.communicate()
        if process.returncode != 0 and session_id is None:
            raise RuntimeError("external CLI failed to start")
        return output or ""
