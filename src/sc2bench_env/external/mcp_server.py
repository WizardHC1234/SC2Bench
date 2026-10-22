"""Loopback MCP server. It forwards tool calls and does not touch the match."""
from __future__ import annotations


import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import urlparse

from sc2bench_env.external.channel import PlayerChannel
from sc2bench_env.interface.tools import ToolSpec


_PROTOCOL = "2025-03-26"


def _tool_schema(spec: ToolSpec) -> dict[str, Any]:
    parameters = dict(spec.parameters or {})
    if parameters.get("type") != "object":
        parameters = {"type": "object", "properties": {}, "additionalProperties": False}
    return {
        "name": spec.name,
        "description": spec.description,
        "inputSchema": parameters,
    }


def _rpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _agent_visible_text(name: str, payload: dict[str, Any]) -> str:
    """The text Codex reads. Status stays on the first line for the match tests."""
    status = payload.get("status")
    if status == "committed":
        return (
            "Status: committed\n"
            "The decision was submitted. Stop and wait for the next decision."
        )
    if status in {"ok", "staged", "no_op", "rejected", "decision_ready"}:
        from sc2bench_env.adapters.llm.adapter import LLMAdapter
        from sc2bench_env.interface.tools import ToolCall, ToolResult

        result = ToolResult(
            status=status,
            data=payload.get("data") if isinstance(payload.get("data"), dict) else {},
            decision_boundary=bool(payload.get("decision_boundary")),
        )
        return LLMAdapter().render_tool_result(ToolCall(name, {}), result)
    return json.dumps(payload, ensure_ascii=False, default=str)


class PlayerEndpoint:
    def __init__(self, channel: PlayerChannel, *, token: Optional[str], tool_timeout: float) -> None:
        self.channel = channel
        self.token = token
        self.tool_timeout = tool_timeout
        self.specs: tuple[ToolSpec, ...] = ()

    def set_specs(self, specs: tuple[ToolSpec, ...]) -> None:
        self.specs = tuple(specs)


class MatchMcpServer:
    """One match, one capability URL per external Agent. The port listens only on loopback."""

    def __init__(self, channels: tuple[PlayerChannel, ...], *, port: int = 0, tool_timeout: float = 120.0) -> None:
        self.endpoints = {
            secrets.token_urlsafe(24): PlayerEndpoint(channel, token=secrets.token_urlsafe(24), tool_timeout=tool_timeout)
            for channel in channels
        }
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.match_mcp = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="sc2bench-mcp", daemon=True)

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    def urls(self) -> tuple[str, ...]:
        keys = tuple(self.endpoints)
        return tuple(
            "http://127.0.0.1:{0}/mcp/{1}".format(self.port, key) for key in keys
        )

    def tokens(self) -> tuple[Optional[str], ...]:
        return tuple(endpoint.token for endpoint in self.endpoints.values())

    def publish_specs(self, player: int, specs: tuple[ToolSpec, ...]) -> None:
        endpoint = tuple(self.endpoints.values())[player]
        endpoint.set_specs(specs)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        for endpoint in self.endpoints.values():
            endpoint.channel.close_match()
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return None

    def do_POST(self) -> None:  # noqa: N802
        endpoint = self._endpoint()
        if endpoint is None:
            self._send(404, {"error": "unknown capability"})
            return
        if not self._authorized(endpoint):
            self._send(401, {"error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            message = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            self._send(400, _rpc_error(None, -32700, "invalid json"))
            return
        if isinstance(message, list):
            replies = [self._dispatch(endpoint, item) for item in message]
            body = [item for item in replies if item is not None]
            self._send(200, body)
            return
        reply = self._dispatch(endpoint, message if isinstance(message, dict) else {})
        if reply is None:
            self._send(202, {})
            return
        self._send(200, reply)

    def _endpoint(self) -> Optional[PlayerEndpoint]:
        path = urlparse(self.path).path.rstrip("/")
        prefix = "/mcp/"
        if not path.startswith(prefix):
            return None
        server = self.server.match_mcp  # type: ignore[attr-defined]
        return server.endpoints.get(path[len(prefix):])

    def _authorized(self, endpoint: PlayerEndpoint) -> bool:
        if not endpoint.token:
            return True
        header = self.headers.get("Authorization", "")
        return header == "Bearer " + endpoint.token

    def _dispatch(self, endpoint: PlayerEndpoint, message: dict[str, Any]) -> Optional[dict[str, Any]]:
        method = message.get("method")
        request_id = message.get("id")
        if request_id is None and method in {"notifications/initialized", "initialized"}:
            return None
        if method == "initialize":
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            version = params.get("protocolVersion") or _PROTOCOL
            return _rpc_result(request_id, {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "sc2bench", "version": "0.1.0"},
            })
        if method == "ping":
            return _rpc_result(request_id, {})
        if method == "tools/list":
            return _rpc_result(request_id, {"tools": [_tool_schema(spec) for spec in endpoint.specs]})
        if method == "tools/call":
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str) or not name:
                return _rpc_error(request_id, -32602, "tool name is required")
            if not isinstance(arguments, dict):
                return _rpc_error(request_id, -32602, "tool arguments must be an object")
            payload = endpoint.channel.submit(name, arguments, timeout=endpoint.tool_timeout)
            rejected = payload.get("status") == "rejected"
            return _rpc_result(request_id, {
                "content": [{"type": "text", "text": _agent_visible_text(name, payload)}],
                "isError": rejected,
            })
        if request_id is None:
            return None
        return _rpc_error(request_id, -32601, "method not found")

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
