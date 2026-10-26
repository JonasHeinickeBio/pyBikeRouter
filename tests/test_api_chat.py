"""``POST /v1/chat`` through the real chat service, with the planning itself stubbed."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from httpx import ASGITransport

import bike_routing_agent.api as api_module
from bike_routing_agent.chat.service import ChatService
from bike_routing_agent.config import Settings
from bike_routing_agent.errors import ProviderUnavailableError
from bike_routing_agent.models import (
    RouteCandidate,
    RouteMetrics,
    RoutePlanAPIRequest,
    RoutePlanResponse,
)
from bike_routing_agent.poi.models import Poi
from bike_routing_agent.poi.service import PoiSearchResult


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=ASGITransport(app=api_module.app), base_url="http://test"
    ) as c:
        yield c


def candidate(rank: int, profile: str = "custom_gravel-v2") -> RouteCandidate:
    return RouteCandidate(
        provider="brouter",
        provider_profile=profile,
        rank=rank,
        score=0.9,
        geometry_geojson={"type": "LineString", "coordinates": [[10.5, 52.2], [10.6, 52.3]]},
        metrics=RouteMetrics(distance_m=30_000.0 + rank, duration_s=4200.0, ascent_m=100.0),
        pros=[f"pro {rank}"],
        cons=[],
    )


class Planned:
    """Replaces ``plan_route`` / ``plan_route_from_text``: remembers what the chat asked."""

    def __init__(self) -> None:
        self.requests: list[RoutePlanAPIRequest] = []
        self.texts: list[Any] = []
        self.response = RoutePlanResponse(
            status="ready",
            route=candidate(1),
            candidates=[candidate(1), candidate(2, "mtb")],
            explanation="best fit",
            artifacts={"gpx_url": "/v1/routes/x.gpx"},
        )

    async def plan(self, request: RoutePlanAPIRequest) -> RoutePlanResponse:
        self.requests.append(request)
        return self.response

    async def plan_text(self, request: Any) -> RoutePlanResponse:
        self.texts.append(request)
        return self.response


@pytest.fixture
def planned(monkeypatch: pytest.MonkeyPatch) -> Planned:
    planned = Planned()
    monkeypatch.setattr(api_module, "plan_route", planned.plan)
    monkeypatch.setattr(api_module, "plan_route_from_text", planned.plan_text)
    monkeypatch.setattr(
        api_module, "_chat_service", api_module.build_chat_service(Settings(_env_file=None))
    )
    return planned


async def test_a_one_line_request_returns_a_reply_and_the_plan_to_draw(client, planned):
    response = await client.post(
        "/v1/chat", json={"message": "from Braunschweig to Goslar by gravel"}
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["session_id"]) == 32 and body["intent"] == "plan" and body["awaiting"] is None
    assert "Here is your route" in body["reply"] and body["suggestions"][0] == "Alternatives"
    assert body["plan"]["status"] == "ready" and len(body["plan"]["candidates"]) == 2
    request = planned.requests[0]
    assert (request.origin, request.destination) == ("Braunschweig", "Goslar")
    assert request.constraints.bike_type == "gravel"


async def test_the_conversation_continues_in_the_same_session(client, planned):
    first = (await client.post("/v1/chat", json={"message": "40 km loop from Goslar"})).json()
    again = await client.post(
        "/v1/chat", json={"message": "make it shorter", "session_id": first["session_id"]}
    )
    body = again.json()
    assert body["session_id"] == first["session_id"] and body["intent"] == "refine"
    assert planned.requests[1].constraints.target_distance_km == 30.0
    picked = (
        await client.post(
            "/v1/chat", json={"message": "the mtb version", "session_id": first["session_id"]}
        )
    ).json()
    assert picked["focus_rank"] == 2 and picked["plan"] is not None
    assert len(planned.requests) == 2  # picking an alternative does not route again


async def test_the_step_by_step_dialogue_over_http(client, planned):
    sid = None
    for text in ["plan a route", "Goslar", "A loop", "30", "gravel"]:
        body = (await client.post("/v1/chat", json={"message": text, "session_id": sid})).json()
        sid = body["session_id"]
    assert body["plan"] is not None and planned.requests[0].constraints.return_to_origin is True


async def test_free_text_uses_the_llm_parser_when_it_is_enabled(client, planned, monkeypatch):
    monkeypatch.setattr(api_module, "_llm_parser", lambda text, timezone=None: {})
    monkeypatch.setattr(
        api_module, "_chat_service", api_module.build_chat_service(Settings(_env_file=None))
    )
    body = (
        await client.post(
            "/v1/chat",
            json={"message": "a relaxed ride around Goslar", "timezone": "Europe/Berlin"},
        )
    ).json()
    assert body["intent"] == "plan_text"
    assert planned.texts[0].text == "a relaxed ride around Goslar"
    assert planned.texts[0].timezone == "Europe/Berlin"


async def test_free_text_without_a_parser_falls_back_to_the_dialogue(client, planned, monkeypatch):
    monkeypatch.setattr(api_module, "_llm_parser", None)
    monkeypatch.setattr(
        api_module, "_chat_service", api_module.build_chat_service(Settings(_env_file=None))
    )
    body = (await client.post("/v1/chat", json={"message": "a relaxed ride around Goslar"})).json()
    assert body["intent"] == "guided" and body["awaiting"] == "guided" and planned.texts == []


async def test_a_request_the_api_would_reject_is_answered_not_a_422(client, planned):
    body = (await client.post("/v1/chat", json={"message": "2000 km loop from Goslar"})).json()
    assert "That did not work" in body["reply"] and body["plan"] is None
    assert planned.requests == []  # rejected before routing


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": ""},
        {"message": "   "},
        {"message": "x" * 501},
        {"message": "hi", "session_id": "not-a-session"},
        {"message": "hi", "timezone": "Mars/Base"},
        {"message": "hi", "session_id": "A" * 32},
        {"message": "hi", "extra": 1},
    ],
)
async def test_bad_chat_requests_are_422(client, planned, payload):
    assert (await client.post("/v1/chat", json=payload)).status_code == 422


async def test_the_chat_is_503_when_switched_off_and_capabilities_say_so(client, monkeypatch):
    monkeypatch.setattr(api_module, "_chat_service", None)
    assert (await client.post("/v1/chat", json={"message": "hi"})).status_code == 503
    assert (await client.get("/v1/capabilities")).json()["chat"] is False
    monkeypatch.setattr(
        api_module, "_chat_service", api_module.build_chat_service(Settings(_env_file=None))
    )
    assert (await client.get("/v1/capabilities")).json()["chat"] is True
    assert api_module.build_chat_service(Settings(_env_file=None, chat_enabled=False)) is None


def test_chat_settings_are_validated():
    with pytest.raises(ValueError):
        Settings(_env_file=None, chat_max_sessions=0)
    assert Settings(_env_file=None).chat_enabled is True


# ------------------------------------------------------------------------------ sights


class StubPois:
    def __init__(self, error: Exception | None = None) -> None:
        self.error, self.calls = error, []

    async def along_route(self, line, categories, *, buffer_m, linked_only=False, **kw):
        self.calls.append((list(line), categories, buffer_m, linked_only))
        if self.error:
            raise self.error
        here = {"lon": 10.5, "lat": 52.2}
        pois = [
            Poi(id="n/1", name="Burg", category="historic", kind="sight", fame=20,
                distance_from_route_m=100.0, **here),
            Poi(id="n/2", name="Unmeasured", category="viewpoint", kind="sight", **here),
            Poi(id="n/3", name="Cafe", category="food", kind="service", fame=9, **here),
        ]  # fmt: skip
        return PoiSearchResult(pois, truncated=False, fame_status="ok")


async def test_the_chat_lists_the_best_known_sights_along_the_route(client, planned, monkeypatch):
    pois = StubPois()
    monkeypatch.setattr(api_module, "_poi_service", pois)
    monkeypatch.setattr(
        api_module, "_chat_service", api_module.build_chat_service(Settings(_env_file=None))
    )
    first = (await client.post("/v1/chat", json={"message": "A to B"})).json()
    assert "What sights are along the way?" in first["suggestions"]
    body = (
        await client.post(
            "/v1/chat",
            json={"message": "what sights are along the way?", "session_id": first["session_id"]},
        )
    ).json()
    assert "- Burg (described in 20 languages), 100 m off the route" in body["reply"]
    assert "Unmeasured" not in body["reply"] and "Cafe" not in body["reply"]  # fame or services
    line, categories, buffer_m, linked_only = pois.calls[0]
    assert line[0] == (10.5, 52.2) and "food" not in categories and linked_only is True


async def test_a_failing_sight_lookup_is_explained(client, planned, monkeypatch):
    monkeypatch.setattr(
        api_module, "_poi_service", StubPois(ProviderUnavailableError("busy", provider="overpass"))
    )
    monkeypatch.setattr(
        api_module, "_chat_service", api_module.build_chat_service(Settings(_env_file=None))
    )
    first = (await client.post("/v1/chat", json={"message": "A to B"})).json()
    body = (
        await client.post(
            "/v1/chat",
            json={"message": "any castles near the route", "session_id": first["session_id"]},
        )
    ).json()
    assert "not available right now" in body["reply"]


def test_the_chat_service_class_is_what_the_api_builds():
    assert isinstance(api_module.build_chat_service(Settings(_env_file=None)), ChatService)


# ------------------------------------------------------------- stored sessions (chat_store_path)


async def test_the_history_of_a_conversation_can_be_fetched(client, planned):
    first = (await client.post("/v1/chat", json={"message": "from Braunschweig to Goslar"})).json()
    response = await client.get(f"/v1/chat/{first['session_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == first["session_id"]
    assert [m["role"] for m in body["messages"]] == ["user", "bot"]
    assert body["messages"][0]["text"] == "from Braunschweig to Goslar"
    assert body["suggestions"] == first["suggestions"]
    assert body["plan"]["status"] == "ready" and body["awaiting"] is None


async def test_an_unknown_or_malformed_session_is_404(client, planned):
    assert (await client.get(f"/v1/chat/{'a' * 32}")).status_code == 404
    assert (await client.get("/v1/chat/not-a-session")).status_code == 404


async def test_the_history_is_503_when_the_chat_is_switched_off(client, monkeypatch):
    monkeypatch.setattr(api_module, "_chat_service", None)
    assert (await client.get(f"/v1/chat/{'a' * 32}")).status_code == 503


async def test_a_configured_store_keeps_conversations_across_a_restart(client, planned, tmp_path):
    path = tmp_path / "chat" / "sessions.sqlite"
    cfg = Settings(_env_file=None, chat_store_path=str(path))
    service = api_module.build_chat_service(cfg)
    assert service is not None and path.is_file()
    first = await service.send("from Braunschweig to Goslar")
    service._checkpointer.close()

    restarted = api_module.build_chat_service(cfg)  # a new process
    assert restarted is not None and restarted.sessions == 1
    api_module._chat_service = restarted  # the fixture restores the original afterwards
    body = (await client.get(f"/v1/chat/{first.session_id}")).json()
    assert body["plan"]["status"] == "ready" and len(body["messages"]) == 2
    restarted._checkpointer.close()


def test_a_blank_store_path_means_memory_only():
    assert Settings(_env_file=None, chat_store_path="  ").chat_store_path == "  "
    service = api_module.build_chat_service(Settings(_env_file=None, chat_store_path="  "))
    assert service is not None and not hasattr(service._checkpointer, "persist")


def test_the_session_lifetime_must_be_positive():
    with pytest.raises(ValueError, match="chat_session_days"):
        Settings(_env_file=None, chat_session_days=0)


def test_a_store_that_cannot_be_opened_falls_back_to_memory_instead_of_failing(tmp_path, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    cfg = Settings(_env_file=None, chat_store_path=str(blocker / "sessions.sqlite"))
    service = api_module.build_chat_service(cfg)
    assert service is not None and not hasattr(service._checkpointer, "persist")
    assert "chat sessions will not be kept" in caplog.text


# ------------------------------------------------------ the request behind a plan (form filling)


async def test_a_plan_comes_with_the_request_it_was_planned_from(client, planned):
    body = (
        await client.post("/v1/chat", json={"message": "from Braunschweig to Goslar by gravel"})
    ).json()
    assert body["request"]["origin"] == "Braunschweig"
    assert body["request"]["destination"] == "Goslar"
    assert body["request"]["constraints"]["bike_type"] == "gravel"


async def test_a_reply_without_a_plan_has_no_request(client, planned):
    body = (await client.post("/v1/chat", json={"message": "plan a route"})).json()
    assert body["plan"] is None and body["request"] is None


async def test_a_change_returns_the_changed_request(client, planned):
    first = (await client.post("/v1/chat", json={"message": "40 km loop from Goslar"})).json()
    again = (
        await client.post(
            "/v1/chat", json={"message": "make it shorter", "session_id": first["session_id"]}
        )
    ).json()
    assert first["request"]["constraints"]["target_distance_km"] == 40.0
    assert again["request"]["constraints"]["target_distance_km"] == 30.0  # what the form shows


async def test_the_stored_history_carries_the_request_too(client, planned):
    first = (await client.post("/v1/chat", json={"message": "from Braunschweig to Goslar"})).json()
    history = (await client.get(f"/v1/chat/{first['session_id']}")).json()
    assert history["request"] == first["request"] and history["request"]["origin"] == "Braunschweig"


# ------------------------------------------------- conversations: summaries and delete


async def _start(client, text="from Braunschweig to Goslar"):
    return (await client.post("/v1/chat", json={"message": text})).json()["session_id"]


async def test_summaries_name_the_conversations_among_the_given_ids(client, planned):
    first = await _start(client, "from Braunschweig to Goslar")
    second = await _start(client, "40 km loop from Goslar")
    response = await client.post(
        "/v1/chat/summaries", json={"session_ids": [first, second, "c" * 32]}
    )
    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert {s["session_id"] for s in sessions} == {first, second}  # the unknown id is left out
    by_id = {s["session_id"]: s for s in sessions}
    assert by_id[first]["title"] == "from Braunschweig to Goslar" and by_id[first]["messages"] == 2
    assert sessions[0]["updated"] >= sessions[1]["updated"]  # most recent first


async def test_summaries_never_list_a_conversation_that_was_not_named(client, planned):
    await _start(client)
    mine = await _start(client, "40 km loop from Goslar")
    body = (await client.post("/v1/chat/summaries", json={"session_ids": [mine]})).json()
    assert [s["session_id"] for s in body["sessions"]] == [mine]
    assert (await client.get("/v1/chat")).status_code in (404, 405)  # there is no list endpoint


@pytest.mark.parametrize(
    "body",
    [
        {"session_ids": ["not-an-id"]},
        {"session_ids": ["a" * 32] * 31},  # more than the limit
        {"session_ids": ["a" * 32], "extra": 1},
        {},
    ],
)
async def test_summaries_reject_a_malformed_request(client, planned, body):
    assert (await client.post("/v1/chat/summaries", json=body)).status_code == 422


async def test_delete_forgets_the_conversation_and_then_it_is_unknown(client, planned):
    sid = await _start(client)
    assert (await client.delete(f"/v1/chat/{sid}")).status_code == 204
    assert (await client.get(f"/v1/chat/{sid}")).status_code == 404
    assert (await client.delete(f"/v1/chat/{sid}")).status_code == 404
    again = await client.post("/v1/chat/summaries", json={"session_ids": [sid]})
    assert again.json() == {"sessions": []}


async def test_delete_of_a_malformed_id_is_404(client, planned):
    assert (await client.delete("/v1/chat/not-an-id")).status_code == 404


async def test_summaries_and_delete_are_503_when_the_chat_is_switched_off(client, monkeypatch):
    monkeypatch.setattr(api_module, "_chat_service", None)
    assert (
        await client.post("/v1/chat/summaries", json={"session_ids": ["a" * 32]})
    ).status_code == 503
    assert (await client.delete(f"/v1/chat/{'a' * 32}")).status_code == 503


# ------------------------------------------------------------------------------ the web form


def _ready_plan_json(planned: Planned) -> dict[str, Any]:
    return planned.response.model_dump(mode="json")


def test_the_form_context_keeps_what_is_usable_and_drops_the_rest(planned):
    plan = _ready_plan_json(planned)
    got = api_module._form_context(
        {
            "request": {"origin": "Goslar", "destination": "Celle"},
            "plan": {**plan, "trace": [{"node": "x", "ms": 1}]},
        }
    )
    assert got is not None
    assert got["request"]["origin"] == "Goslar" and "departure_time" not in got["request"]
    assert got["plan"]["status"] == "ready" and got["plan"]["trace"] is None
    assert api_module._form_context({"request": {"origin": ""}}) is None  # a blank form
    assert api_module._form_context({"request": {"origin": "A"}}) is None  # no destination
    assert api_module._form_context({"plan": {**plan, "status": "no_route"}}) is None
    assert api_module._form_context({"plan": {"status": "ready", "route": 5}}) is None
    for nothing in (None, {}, "x", 5, ["request"], {"unknown": 1}):
        assert api_module._form_context(nothing) is None  # type: ignore[arg-type]
    only_request = api_module._form_context(
        {"request": {"origin": "A", "destination": "B"}, "plan": "junk"}
    )
    assert only_request is not None and set(only_request) == {"request"}


async def test_the_form_request_in_the_body_reaches_the_chat(client, planned):
    body = {
        "message": "make it shorter",
        "context": {
            "request": {
                "origin": "Goslar",
                "constraints": {"target_distance_km": 40, "return_to_origin": True},
            },
            "plan": _ready_plan_json(planned),
        },
    }
    response = await client.post("/v1/chat", json=body)
    assert response.status_code == 200
    assert planned.requests[0].origin == "Goslar"
    assert planned.requests[0].constraints.target_distance_km == 30.0


async def test_a_useless_context_never_stops_the_message(client, planned):
    for context in ({"request": {"origin": ""}}, {"plan": 5}, {}, {"x": [1]}):
        response = await client.post(
            "/v1/chat", json={"message": "from Braunschweig to Goslar", "context": context}
        )
        assert response.status_code == 200 and response.json()["plan"] is not None
