"""``bike-router mcp``: a real process, a real stdio client, a real (fake) API over HTTP."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bike_routing_agent.cli import main

from .fixtures import ready_plan


class FakeApi(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeApi.seen.append({"path": self.path, "body": body})
        if self.path == "/v1/route/plan":
            payload, code = ready_plan(), 200
        else:
            payload, code = {"detail": "not here"}, 404
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # keep the test output clean
        pass


@pytest.fixture
def api_url():
    FakeApi.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def start(api: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "bike_routing_agent.cli", "mcp", "serve", "--api", api],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


class Client:
    def __init__(self, process: subprocess.Popen) -> None:
        self.process, self.next_id = process, 0

    def send(self, method: str, params: dict | None = None, *, notify: bool = False):
        message: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            self.next_id += 1
            message["id"] = self.next_id
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()
        return None if notify else json.loads(self.process.stdout.readline())

    def tool(self, name: str, **arguments):
        reply = self.send("tools/call", {"name": name, "arguments": arguments})
        return reply["result"]


def test_a_client_can_plan_and_follow_up_through_the_real_process(api_url):
    process = start(api_url)
    client = Client(process)
    try:
        info = client.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        assert info["result"]["serverInfo"]["name"] == "bike-router"
        client.send("notifications/initialized", notify=True)
        names = [t["name"] for t in client.send("tools/list")["result"]["tools"]]
        assert names == ["plan_route", "explain_route", "route_weather", "sights_along_route"]

        plan = client.tool("plan_route", origin="52.26,10.52", destination="Goslar")
        assert plan["isError"] is False
        data = plan["structuredContent"]
        assert data["status"] == "ready" and data["route"]["distance_km"] == 30.6
        assert data["files"]["gpx"] == f"{api_url}/v1/routes/a.gpx"
        assert FakeApi.seen[0]["body"]["origin"] == {"lat": 52.26, "lon": 10.52}

        weather = client.tool("route_weather", plan_ref=data["plan_ref"])
        assert weather["structuredContent"]["available"] is True

        sights = client.tool("sights_along_route", plan_ref=data["plan_ref"])
        assert sights["isError"] is True and "HTTP 404" in sights["structuredContent"]["error"]
        assert client.send("ping")["result"] == {}
    finally:
        process.stdin.close()
    assert process.wait(timeout=10) == 0
    out, err = process.stdout.read(), process.stderr.read()
    assert out == ""  # everything was read as replies: stdout carried protocol messages only
    assert "ready" in err  # the one line of diagnostics goes to stderr


def test_an_unreachable_api_is_an_error_result_not_a_crash():
    process = start("http://127.0.0.1:1")
    client = Client(process)
    try:
        client.send("initialize", {"protocolVersion": "2025-06-18"})
        result = client.tool("plan_route", origin="A", destination="B")
        assert result["isError"] is True
        assert "cannot reach the pyBikeRouter API" in result["structuredContent"]["error"]
        assert client.send("ping")["result"] == {}  # still serving
    finally:
        process.stdin.close()
    assert process.wait(timeout=10) == 0


def test_garbage_on_stdin_gets_an_error_and_the_server_goes_on(api_url):
    process = start(api_url)
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write("{{ not json\n")
        process.stdin.flush()
        assert json.loads(process.stdout.readline())["error"]["code"] == -32700
        assert Client(process).send("ping")["result"] == {}
    finally:
        process.stdin.close()
    assert process.wait(timeout=10) == 0


def run(*argv: str, stdin: str = ""):
    out, err = io.StringIO(), io.StringIO()
    code = main(["mcp", *argv], stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_config_prints_the_entry_for_a_clients_configuration():
    code, out, _ = run("config", "--api", "http://127.0.0.1:8765/")
    assert code == 0
    entry = json.loads(out)["mcpServers"]["bike-router"]
    assert entry == {
        "command": "bike-router",
        "args": ["mcp", "serve", "--api", "http://127.0.0.1:8765"],
    }


def test_the_api_must_be_an_http_url_and_can_come_from_the_environment(monkeypatch):
    for bad in ("localhost:8000", "file:///etc/passwd", "ftp://x", ""):
        code, _, err = run("serve", "--api", bad)
        assert code == 2 and "http(s) URL" in err
    monkeypatch.setenv("BIKE_ROUTER_API_URL", "https://routes.example")
    entry = json.loads(run("config")[1])["mcpServers"]["bike-router"]
    assert entry["args"][-1] == "https://routes.example"
    monkeypatch.delenv("BIKE_ROUTER_API_URL")
    assert json.loads(run("config")[1])["mcpServers"]["bike-router"]["args"][-1] == (
        "http://127.0.0.1:8000"
    )
