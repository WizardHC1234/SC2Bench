"""One player's queue. MCP threads wait here; only the match thread touches the turn."""
from __future__ import annotations


import queue
import threading
from typing import Any, Optional


def _error(code: str, message: str) -> dict[str, Any]:
    return {
        "status": "rejected",
        "decision_boundary": False,
        "data": {"error": {"code": code, "message": message}},
    }


class Command:
    def __init__(self, turn_id: str, name: str, arguments: dict[str, Any]) -> None:
        self.turn_id = turn_id
        self.name = name
        self.arguments = arguments
        self._ready = threading.Event()
        self.response: Optional[dict[str, Any]] = None

    def respond(self, payload: dict[str, Any]) -> None:
        if self._ready.is_set():
            return
        self.response = payload
        self._ready.set()

    def wait(self, timeout: float) -> dict[str, Any]:
        if not self._ready.wait(timeout):
            return _error("tool_timeout", "The match did not answer this tool call in time")
        return self.response or _error("tool_timeout", "The match did not answer this tool call")


class PlayerChannel:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.queue: queue.Queue[Command] = queue.Queue()
        self.active_turn_id: Optional[str] = None
        self.active_input: Any = None
        self.closed_turn = False
        self.match_closed = False
        self.committed = False

    def publish(self, turn_id: str, agent_input: Any) -> None:
        with self._lock:
            self.active_turn_id = turn_id
            self.active_input = agent_input
            self.closed_turn = False
            self.committed = False

    def close_turn(self) -> None:
        with self._lock:
            self.closed_turn = True
            self.active_input = None

    def mark_committed(self) -> None:
        with self._lock:
            self.committed = True

    def close_match(self) -> None:
        with self._lock:
            self.match_closed = True
            self.closed_turn = True
            self.active_input = None
        while True:
            try:
                command = self.queue.get_nowait()
            except queue.Empty:
                return
            command.respond(_error("no_active_turn", "The match is no longer accepting tool calls"))

    def submit(self, name: str, arguments: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        with self._lock:
            if self.match_closed or self.active_turn_id is None:
                return _error("no_active_turn", "There is no active decision")
            if self.closed_turn:
                return _error("turn_closed", "This decision was already submitted")
            command = Command(self.active_turn_id, name, arguments)
            self.queue.put(command)
        return command.wait(timeout)

    def take(self, timeout: float) -> Optional[Command]:
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None
