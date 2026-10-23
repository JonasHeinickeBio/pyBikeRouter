"""The life of a plan request: traced per step, cached briefly, cancelled when nobody waits,
streamed as it happens -- and a newer chat message replacing a running one."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

import bike_routing_agent.api as api_module
from bike_routing_agent.api import _export_dir, _until_disconnected, app
from bike_routing_agent.chat.service import TurnSuperseded
from bike_routing_agent.models import RoutePlanAPIRequest
from bike_routing_agent.tracing import STEP_LABELS, server_timing, step, total_ms, traced

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
ORS_URL = "https://api.openrouteservice.org/v2/directions/cycling-regular/geojson"
PLAN = {"origin": "Braunschweig Hauptbahnhof", "destination": "Wolfenbuettel"}


@pytest.fixture(autouse=True)
def _clean_export_dir():
    yield
    import shutil

    shutil.rmtree(_export_dir, ignore_errors=True)


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
def engines(ors_directions_response, nominatim_single_response):
    """Nominatim and ORS answering; ``engines.ors.call_count`` says how often routing ran."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(NOMINATIM_URL).mock(
            return_value=httpx.Response(200, json=nominatim_single_response[:1])
        )
        route = mock.post(ORS_URL).mock(
            return_value=httpx.Response(200, json=ors_directions_response)
        )
        mock.ors = route
        yield mock


# ------------------------------------------------------------------------------ tracing


async def test_a_traced_node_adds_its_step_and_keeps_what_the_node_said():
    async def node(state):
        return {"status": "in_progress", "other": 1}

    result = await traced("route_with_provider", node)({})
    assert result["other"] == 1 and result["status"] == "in_progress"
    (only,) = result["trace"]
    assert only["node"] == "route_with_provider" and only["ok"] is True
    assert only["status"] == "in_progress" and only["ms"] >= 0


async def test_a_sync_node_and_a_node_that_says_nothing_are_passed_through():
    assert await traced("a", lambda state: None)({}) is None
    result = await traced("a", lambda state: {"x": 1})({})
    assert result["x"] == 1 and result["trace"][0]["status"] is None


async def test_a_failing_node_is_logged_and_raises_unchanged(caplog):
    async def boom(state):
        raise RuntimeError("no route")

    caplog.set_level(logging.INFO, logger="bike_routing_agent.tracing")
    with pytest.raises(RuntimeError, match="no route"):
        await traced("route_with_provider", boom)({})
    assert "node=route_with_provider" in caplog.text and "failed" in caplog.text


def test_server_timing_lists_every_step_and_the_total():
    trace = [step("parse_request", 1.04, "x"), step("route_with_provider", 250.0, "x")]
    assert total_ms(trace) == 251.0
    assert (
        server_timing(trace)
        == "parse_request;dur=1.0, route_with_provider;dur=250.0, total;dur=251.0"
    )
    assert server_timing([]) == ""


def test_every_graph_node_has_a_label_a_person_can_read():
    from bike_routing_agent.graph import build_graph  # noqa: F401  (nodes are registered there)

    assert all(label and label[0].isupper() for label in STEP_LABELS.values())
    assert len(STEP_LABELS) == 11


async def test_the_response_carries_the_trace_only_when_asked_and_the_header_always(
    client, engines
):
    plain = await client.post("/v1/route/plan", json=PLAN)
    assert plain.status_code == 200 and plain.json()["trace"] is None
    timing = plain.headers["server-timing"]
    assert "route_with_provider;dur=" in timing and "total;dur=" in timing

    traced_response = await client.post("/v1/route/plan?trace=true", json=PLAN)
    nodes = [s["node"] for s in traced_response.json()["trace"]]
    assert nodes[0] == "parse_request" and nodes[-1] == "explain_and_export"
    assert set(nodes) <= set(STEP_LABELS) and "route_with_provider" in nodes
    assert all(s["ms"] >= 0 and s["ok"] for s in traced_response.json()["trace"])


# ------------------------------------------------------------------------------ the plan cache


@pytest.fixture
def graph_runs(monkeypatch):
    """How often the planning graph really ran (a cache hit does not run it)."""
    runs: list[dict[str, Any]] = []
    original = api_module._run_graph

    async def counting(raw_input, on_event=None):
        runs.append(raw_input)
        return await original(raw_input, on_event)

    monkeypatch.setattr(api_module, "_run_graph", counting)
    return runs


@pytest.fixture
def cached(monkeypatch):
    monkeypatch.setattr(api_module.settings, "plan_cache_ttl_s", 60.0)
    api_module._plan_cache = api_module.NamespacedCache(api_module.InMemoryTTLCache(), "t")
    yield
    api_module._plan_cache = api_module.NamespacedCache(api_module.InMemoryTTLCache(), "t")


async def test_the_same_request_twice_routes_once(client, engines, cached, graph_runs):
    first = (await client.post("/v1/route/plan", json=PLAN)).json()
    second = (await client.post("/v1/route/plan", json=PLAN)).json()
    assert len(graph_runs) == 1 and engines.ors.call_count == 1
    assert first["served_from_cache"] is False and first["cache_age_s"] is None
    assert second["served_from_cache"] is True and second["cache_age_s"] >= 0
    assert second["route"]["geometry_geojson"] == first["route"]["geometry_geojson"]
    assert second["explanation"] == first["explanation"]


async def test_a_different_request_is_not_a_hit(client, engines, cached, graph_runs):
    later = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0).isoformat()
    await client.post("/v1/route/plan", json=PLAN)
    await client.post("/v1/route/plan", json={**PLAN, "constraints": {"bike_type": "road"}})
    await client.post("/v1/route/plan", json={**PLAN, "departure_time": later})
    assert len(graph_runs) == 3


async def test_a_plan_that_did_not_get_ready_is_never_cached(
    client, nominatim_ambiguous_response, cached, graph_runs
):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(NOMINATIM_URL).mock(
            return_value=httpx.Response(200, json=nominatim_ambiguous_response)
        )
        for _ in range(2):
            body = (await client.post("/v1/route/plan", json=PLAN)).json()
            assert body["status"] != "ready" and body["served_from_cache"] is False
    assert len(graph_runs) == 2  # asked again, not remembered


async def test_a_plan_for_now_is_shared_only_inside_one_window(monkeypatch):
    raw = {"origin": "A", "destination": "B", "departure_time": None}
    monkeypatch.setattr(api_module.time, "time", lambda: 1_000_000.0)
    same_window = api_module._plan_cache_key(raw)
    monkeypatch.setattr(api_module.time, "time", lambda: 1_000_000.0 + 60)
    assert api_module._plan_cache_key(raw) == same_window
    monkeypatch.setattr(api_module.time, "time", lambda: 1_000_000.0 + api_module.NOW_WINDOW_S)
    assert api_module._plan_cache_key(raw) != same_window
    # a fixed departure does not move with the clock
    fixed = {**raw, "departure_time": "2030-05-01T09:00:00+00:00"}
    assert api_module._plan_cache_key(fixed) == api_module._plan_cache_key(fixed)


async def test_an_unreadable_cache_entry_is_a_miss(client, engines, cached):
    raw_key = None

    original = api_module._plan_cache_key

    def spy(raw):
        nonlocal raw_key
        raw_key = original(raw)
        return raw_key

    api_module._plan_cache_key = spy
    try:
        await client.post("/v1/route/plan", json=PLAN)
        await api_module._plan_cache.set(raw_key, {"response": "garbage"}, ttl_s=60)
        again = (await client.post("/v1/route/plan", json=PLAN)).json()
    finally:
        api_module._plan_cache_key = original
    assert again["status"] == "ready" and again["served_from_cache"] is False


async def test_the_cache_is_off_with_a_zero_ttl(client, engines, graph_runs):
    assert api_module.settings.plan_cache_ttl_s == 0  # the suite's default (tests/conftest.py)
    await client.post("/v1/route/plan", json=PLAN)
    await client.post("/v1/route/plan", json=PLAN)
    assert len(graph_runs) == 2


# ------------------------------------------------------------------------------ cancellation


class Gone:
    """A request whose client goes away after ``after`` polls."""

    def __init__(self, after: int) -> None:
        self.after, self.polls = after, 0

    async def is_disconnected(self) -> bool:
        self.polls += 1
        return self.polls > self.after


class StaysConnected:
    async def is_disconnected(self) -> bool:
        return False


async def test_a_client_that_goes_away_cancels_the_work_and_gets_499(monkeypatch):
    monkeypatch.setattr(api_module, "DISCONNECT_POLL_S", 0.01)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def slow():
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(api_module.HTTPException) as caught:
        await asyncio.wait_for(_until_disconnected(Gone(after=2), slow()), timeout=5)
    assert caught.value.status_code == 499
    assert started.is_set() and cancelled.is_set()


async def test_work_that_finishes_is_returned_and_the_watcher_stops(monkeypatch):
    monkeypatch.setattr(api_module, "DISCONNECT_POLL_S", 0.01)

    async def quick():
        await asyncio.sleep(0.03)
        return "done"

    assert await _until_disconnected(StaysConnected(), quick()) == "done"
    await asyncio.sleep(0.05)
    assert not [t for t in asyncio.all_tasks() if t.get_coro().__qualname__.endswith("watch")]


async def test_a_cancelled_request_cancels_its_work_too(monkeypatch):
    monkeypatch.setattr(api_module, "DISCONNECT_POLL_S", 0.01)
    cancelled = asyncio.Event()

    async def slow():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    outer = asyncio.ensure_future(_until_disconnected(StaysConnected(), slow()))
    await asyncio.sleep(0.05)
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer
    assert cancelled.is_set()


async def test_the_plan_endpoint_is_guarded_by_the_disconnect_watch(monkeypatch):
    """Found with the real graph: routing is awaited inside it, so cancelling stops the plan."""
    monkeypatch.setattr(api_module, "DISCONNECT_POLL_S", 0.01)
    cancelled = asyncio.Event()

    async def slow_plan(request, *, on_event=None):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(api_module, "plan_route", slow_plan)
    request = RoutePlanAPIRequest(origin="A", destination="B")
    with pytest.raises(api_module.HTTPException) as caught:
        await asyncio.wait_for(
            api_module.plan_route_endpoint(request, Gone(after=1), api_module.Response()),
            timeout=5,
        )
    assert caught.value.status_code == 499 and cancelled.is_set()


# ------------------------------------------------------------------------------ the stream


def events(text: str) -> list[tuple[str, Any]]:
    out = []
    for block in text.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


async def test_the_stream_tells_the_steps_then_the_route_then_the_result(client, engines):
    response = await client.post("/v1/route/plan/stream", json=PLAN)
    assert response.headers["content-type"].startswith("text/event-stream")
    got = events(response.text)
    names = [name for name, _ in got]
    assert names[-1] == "result" and names.count("result") == 1
    steps = [data["node"] for name, data in got if name == "step"]
    assert steps[0] == "parse_request" and steps[-1] == "explain_and_export"
    assert all(data["label"] for name, data in got if name == "step")
    # the best route is told before the forecast and the files are done
    first_route = names.index("route")
    assert got[first_route][1]["geometry_geojson"]["type"] == "LineString"
    assert first_route < names.index("result")
    later = [data["node"] for name, data in got[first_route:] if name == "step"]
    assert "explain_and_export" in later
    result = got[-1][1]
    assert result["status"] == "ready" and result["trace"][0]["node"] == "parse_request"


async def test_a_stream_that_crashes_ends_with_an_error_event(client, monkeypatch):
    async def broken(request, *, on_event=None):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(api_module, "plan_route", broken)
    got = events((await client.post("/v1/route/plan/stream", json=PLAN)).text)
    assert [name for name, _ in got] == ["error"]
    assert "engine exploded" not in json.dumps(got)  # no internals in the stream


async def test_closing_the_stream_cancels_the_plan(monkeypatch):
    cancelled, started = asyncio.Event(), asyncio.Event()

    async def slow(request, *, on_event=None):
        await on_event("parse_request", {"status": "in_progress", "trace": [step("p", 1, None)]})
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(api_module, "plan_route", slow)
    streaming = await api_module.plan_route_stream(RoutePlanAPIRequest(origin="A", destination="B"))
    iterator = streaming.body_iterator
    first = await asyncio.wait_for(iterator.__anext__(), timeout=5)
    assert first.startswith("event: step")
    await started.wait()
    await iterator.aclose()  # what the server does when the browser closes the connection
    assert cancelled.is_set()


# ------------------------------------------------------------------------------ chat


async def test_the_chat_endpoint_says_409_when_its_message_was_replaced(monkeypatch):
    async def replaced(*args, **kwargs):
        raise TurnSuperseded

    class Stub:
        send = staticmethod(replaced)

    monkeypatch.setattr(api_module, "_chat_service", Stub())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        response = await c.post("/v1/chat", json={"message": "hello"})
    assert response.status_code == 409 and "replaced" in response.json()["detail"]
