"""The MCP protocol layer: what a client may rely on, and that no input can kill the server."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from bike_routing_agent.mcp_server.protocol import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    SUPPORTED_VERSIONS,
    McpServer,
)


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def definitions(self) -> list[dict[str, Any]]:
        return [{"name": "echo", "description": "d", "inputSchema": {"type": "object"}}]

    def call(self, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        self.calls.append((name, arguments))
        if name == "echo":
            return {"said": arguments.get("text")}, False
        if name == "fails":
            return {"error": "no"}, True
        if name == "explodes":
            raise RuntimeError("bug in the tool")
        raise KeyError(name)


@pytest.fixture
def server() -> McpServer:
    return McpServer(FakeTools(), name="bike-router", version="9.9", instructions="be careful")


def request(method: str, params: Any = None, id: Any = 1) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def test_initialize_answers_with_the_clients_version_when_it_is_supported(server):
    for version in SUPPORTED_VERSIONS:
        result = server.handle(request("initialize", {"protocolVersion": version}))["result"]
        assert result["protocolVersion"] == version
    result = server.handle(request("initialize", {"protocolVersion": "1999-01-01"}))["result"]
    assert result["protocolVersion"] == SUPPORTED_VERSIONS[0]  # the newest it knows
    assert result["serverInfo"] == {"name": "bike-router", "version": "9.9"}
    assert result["instructions"] == "be careful"
    assert result["capabilities"] == {"tools": {"listChanged": False}}  # tools only, nothing more


def test_initialize_without_a_version_still_works(server):
    assert "result" in server.handle(request("initialize"))


def test_notifications_are_never_answered(server):
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert (
        server.handle({"jsonrpc": "2.0", "method": "notifications/whatever", "params": {}}) is None
    )
    assert (
        server.handle({"jsonrpc": "2.0", "method": "tools/list"}) is None
    )  # no id: a notification


def test_ping_and_tools_list(server):
    assert server.handle(request("ping", id="a")) == {"jsonrpc": "2.0", "id": "a", "result": {}}
    tools = server.handle(request("tools/list"))["result"]["tools"]
    assert [t["name"] for t in tools] == ["echo"]


def test_a_tool_result_carries_the_data_as_text_and_as_structured_content(server):
    result = server.handle(request("tools/call", {"name": "echo", "arguments": {"text": "hé"}}))[
        "result"
    ]
    assert result["isError"] is False
    assert result["structuredContent"] == {"said": "hé"}
    (block,) = result["content"]
    assert block["type"] == "text" and json.loads(block["text"]) == {"said": "hé"}
    assert "hé" in block["text"]  # not escaped to é: readable by a model


def test_a_tool_that_reports_an_error_is_an_error_result_not_a_protocol_error(server):
    result = server.handle(request("tools/call", {"name": "fails"}))["result"]
    assert result["isError"] is True and result["structuredContent"] == {"error": "no"}


def test_a_tool_that_raises_is_an_error_result_and_the_server_lives(server):
    reply = server.handle(request("tools/call", {"name": "explodes", "arguments": {}}))
    assert reply["result"]["isError"] is True
    assert "bug in the tool" not in json.dumps(reply)  # internals stay in the server's log
    assert "result" in server.handle(request("ping"))


def test_a_missing_tool_and_bad_call_params_are_protocol_errors(server):
    unknown = server.handle(request("tools/call", {"name": "nope"}))
    assert unknown["error"]["code"] == INVALID_PARAMS and "nope" in unknown["error"]["message"]
    for params in ({}, {"name": 5}, {"name": "echo", "arguments": [1]}, {"arguments": {}}):
        assert server.handle(request("tools/call", params))["error"]["code"] == INVALID_PARAMS
    assert server.handle(request("tools/call", ["x"]))["error"]["code"] == INVALID_PARAMS


def test_unknown_methods_and_malformed_messages(server):
    assert server.handle(request("resources/list"))["error"]["code"] == METHOD_NOT_FOUND
    assert server.handle(request("prompts/list"))["error"]["code"] == METHOD_NOT_FOUND
    for bad in ([request("ping")], "ping", 5, None):
        assert server.handle(bad)["error"]["code"] == INVALID_REQUEST  # no batches, no scalars
    assert server.handle({"id": 1, "method": "ping"})["error"]["code"] == INVALID_REQUEST
    assert server.handle({"jsonrpc": "1.0", "id": 1, "method": "ping"})["error"]["code"] == (
        INVALID_REQUEST
    )
    assert server.handle({"jsonrpc": "2.0", "id": 7, "method": 5})["error"]["code"] == (
        INVALID_REQUEST
    )


def test_the_id_of_the_request_comes_back_whatever_its_type(server):
    for request_id in (0, "abc", 12345678901234):
        assert server.handle(request("ping", id=request_id))["id"] == request_id


def test_the_loop_answers_line_by_line_and_survives_garbage():
    server = McpServer(FakeTools(), name="n", version="1")
    lines = [
        json.dumps(request("initialize", {"protocolVersion": "2025-06-18"})),
        "",
        "this is not json",
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps(request("tools/call", {"name": "echo", "arguments": {"text": "x"}}, id=2)),
        '{"jsonrpc": "2.0", "id": 3, "method": "ping"}',
    ]
    out = io.StringIO()
    server.serve(io.StringIO("\n".join(lines) + "\n"), out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r.get("id") for r in replies] == [1, None, 2, 3]  # no answer to the notification
    assert replies[1]["error"]["code"] == PARSE_ERROR
    assert replies[2]["result"]["structuredContent"] == {"said": "x"}
    assert all(
        "\n" not in line for line in out.getvalue().rstrip("\n").splitlines()
    )  # one per line


def test_a_line_nested_deeper_than_python_parses_is_a_parse_error_not_the_end():
    server = McpServer(FakeTools(), name="n", version="1")
    deep = "[" * 100_000 + "]" * 100_000
    out = io.StringIO()
    server.serve(io.StringIO(deep + "\n" + json.dumps(request("ping", id=9)) + "\n"), out)
    first, second = (json.loads(line) for line in out.getvalue().splitlines())
    assert first["error"]["code"] == PARSE_ERROR
    assert second["id"] == 9 and second["result"] == {}  # and the server is still serving
