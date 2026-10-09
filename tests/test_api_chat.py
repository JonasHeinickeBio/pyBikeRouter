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
    body = (
        await client.post("/v1/chat", json={"message": "30 km loop from Goslar past 2 castles"})
    ).json()
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
        {"message": "hi", "extra": 1, "session_id": "A" * 32},
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
