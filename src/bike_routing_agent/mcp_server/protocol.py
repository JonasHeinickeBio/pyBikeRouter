"""The Model Context Protocol over stdio, as far as a tool server needs it.

Newline-delimited JSON-RPC 2.0 on stdin/stdout: ``initialize`` (version negotiation), the
``initialized`` notification, ``ping``, ``tools/list`` and ``tools/call``. Nothing else is offered
(no resources, prompts, sampling or logging), so nothing else is advertised. Standard output carries
protocol messages only; diagnostics go to standard error.

Written out here instead of pulling in an SDK: it is a few dozen lines, the service has no other use
for one, and the behaviour is pinned by tests that talk to a real subprocess.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import IO, Any, Protocol

logger = logging.getLogger(__name__)

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")  # newest first

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class ToolProvider(Protocol):
    def definitions(self) -> list[dict[str, Any]]: ...

    def call(self, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """The result as data and whether it is an error the agent should see (not a protocol
        error). Raises ``KeyError`` for a tool that does not exist."""
        ...


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class McpServer:
    def __init__(
        self, tools: ToolProvider, *, name: str, version: str, instructions: str | None = None
    ) -> None:
        self._tools = tools
        self._info = {"name": name, "version": version}
        self._instructions = instructions

    def handle(self, message: Any) -> dict[str, Any] | None:
        """The response to one message, or ``None`` when it needs none (a notification)."""
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "a JSON-RPC message is an object (no batches)")
        request_id = message.get("id")
        method = message.get("method")
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return _error(request_id, INVALID_REQUEST, "not a JSON-RPC 2.0 request")
        if "id" not in message:  # a notification: never answered, whatever it is
            return None
        params = message.get("params")
        if params is not None and not isinstance(params, dict):
            return _error(request_id, INVALID_PARAMS, "params must be an object")
        handler: Callable[[Any, dict[str, Any]], dict[str, Any]] | None = {
            "initialize": self._initialize,
            "ping": lambda _id, _params: {"jsonrpc": "2.0", "id": _id, "result": {}},
            "tools/list": self._list,
            "tools/call": self._call,
        }.get(method)
        if handler is None:
            return _error(request_id, METHOD_NOT_FOUND, f"method not found: {method}")
        return handler(request_id, params or {})

    # ------------------------------------------------------------------------------ methods

    def _initialize(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        wanted = params.get("protocolVersion")
        version = wanted if wanted in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        result: dict[str, Any] = {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": self._info,
        }
        if self._instructions:
            result["instructions"] = self._instructions
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _list(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": self._tools.definitions()}}

    def _call(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _error(request_id, INVALID_PARAMS, "tools/call needs a name and arguments")
        try:
            data, is_error = self._tools.call(name, arguments)
        except KeyError:
            return _error(request_id, INVALID_PARAMS, f"unknown tool: {name}")
        except Exception:  # a tool bug is an error result, never a dead server
            logger.exception("tool %s failed", name)
            data, is_error = {"error": "the tool failed unexpectedly; see the server's log"}, True
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        result = {
            "content": [{"type": "text", "text": text}],
            "structuredContent": data,
            "isError": is_error,
        }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    # ------------------------------------------------------------------------------ the loop

    def serve(self, stdin: IO[str], stdout: IO[str]) -> None:
        """Answer messages until the client closes stdin."""
        for line in stdin:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except ValueError:
                response: dict[str, Any] | None = _error(None, PARSE_ERROR, "invalid JSON")
            else:
                response = self.handle(message)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                stdout.flush()
