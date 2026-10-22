"""Keep one Codex SDK thread alive in a helper process for the whole match."""
from __future__ import annotations


import json
import os
import subprocess
import sys
from typing import Any, Optional

from sc2bench_env.external.adapters.base import codex_directories, write_codex_config
from sc2bench_env.external.process import stop_process, text_pipe_kwargs
from sc2bench_env.external.turn import AgentContext, AgentHandle, TurnInput, TurnRun


class CodexSdkAdapter:
    adapter_type = "codex_sdk"

    def __init__(self, *, python: Optional[str] = None, grace_seconds: float = 5.0) -> None:
        self.python = python or sys.executable
        self.grace_seconds = grace_seconds

    def open(self, context: AgentContext, first_input: TurnInput) -> AgentHandle:
        _, _, workspace = codex_directories(context.work_dir)
        process = self._start(context)
        reply = self._request(process, {
            "op": "open",
            "model": context.model,
            "cwd": str(workspace),
            "message": first_input.render(first=True),
        })
        thread_id = reply.get("thread_id")
        if not reply.get("ok") or not isinstance(thread_id, str) or not thread_id:
            stop_process(process, grace_seconds=self.grace_seconds)
            raise RuntimeError(reply.get("error") or "Codex SDK did not start a thread")
        return AgentHandle(thread_id, self.adapter_type, {"process": process, "context": context})

    def run_turn(self, handle: AgentHandle, turn_input: TurnInput) -> TurnRun:
        process = handle.extras.get("process")
        reply = self._request(process, {"op": "turn", "message": turn_input.render(first=False)})
        if not reply.get("ok") or reply.get("thread_id") != handle.opaque_resume_id:
            raise RuntimeError(reply.get("error") or "Codex SDK lost the match thread")
        return TurnRun()

    def close(self, handle: AgentHandle) -> None:
        process = handle.extras.get("process")
        if isinstance(process, subprocess.Popen) and process.poll() is None and process.stdin is not None:
            try:
                process.stdin.write(json.dumps({"op": "close"}) + "\n")
                process.stdin.flush()
            except OSError:
                pass
        stop_process(process if isinstance(process, subprocess.Popen) else None, grace_seconds=self.grace_seconds)

    def _start(self, context: AgentContext) -> subprocess.Popen:
        work, home, workspace = codex_directories(context.work_dir)
        write_codex_config(home, mcp_url=context.mcp_url, use_token=bool(context.mcp_token))
        env = os.environ.copy()
        env["CODEX_HOME"] = str(home)
        env.pop("SC2BENCH_MCP_TOKEN", None)
        if context.mcp_token:
            env["SC2BENCH_MCP_TOKEN"] = context.mcp_token
        return subprocess.Popen(
            [self.python, "-m", "sc2bench_env.external.adapters.codex_sdk_helper"],
            cwd=str(workspace),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **text_pipe_kwargs(),
        )

    def _request(self, process: Any, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(process, subprocess.Popen) or process.stdin is None or process.stdout is None:
            raise RuntimeError("Codex SDK helper is not running")
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
        if not line:
            raise RuntimeError("Codex SDK helper closed")
        reply = json.loads(line)
        if not isinstance(reply, dict):
            raise RuntimeError("Codex SDK helper returned a non-object")
        return reply
