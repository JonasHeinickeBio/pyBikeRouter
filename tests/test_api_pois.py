"""POI endpoints and the famous-POI route stops, end to end through the API."""

import httpx
import pytest
from httpx import ASGITransport

import bike_routing_agent.api as api_module
from bike_routing_agent.errors import ProviderUnavailableError
from bike_routing_agent.models import PoiStopsRequest, RoutePlanAPIRequest
from bike_routing_agent.poi.models import Poi, PoiInfo
from bike_routing_agent.poi.service import PoiSearchResult


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=ASGITransport(app=api_module.app), base_url="http://test"
    ) as c:
        yield c


def poi(ident="node/1", **kw):
    base = {"category": "viewpoint", "kind": "sight", "lon": 10.1, "lat": 47.5}
    return Poi(id=ident, **{**base, **kw})


class StubService:
    def __init__(self, pois=None, error=None):
        self.pois = pois if pois is not None else [poi(fame=7, distance_from_route_m=12.0)]
        self.error = error
        self.calls = []

    async def along_route(self, line, categories, *, buffer_m, per_category_limit=None, **kw):
        self.calls.append(("along", list(line), categories, buffer_m, per_category_limit))
        if self.error:
            raise self.error
        return PoiSearchResult(self.pois, truncated=True, fame_status="partial")

    async def in_bbox(self, bbox, categories, *, per_category_limit=None):
        self.calls.append(("bbox", bbox, categories, per_category_limit))
        if self.error:
            raise self.error
        return PoiSearchResult(self.pois, truncated=False, fame_status="ok")

    async def info(self, **kw):
        self.calls.append(("info", kw))
        return PoiInfo(title="T", extract="E")


@pytest.fixture
def stub(monkeypatch):
    service = StubService()
    monkeypatch.setattr(api_module, "_poi_service", service)
    return service


COORDS = [[10.0, 47.5], [10.1, 47.51], [10.2, 47.5]]


async def test_categories_are_listed_for_building_a_filter(client):
    body = (await client.get("/v1/pois/categories")).json()
    keys = {c["key"]: c for c in body}
    assert keys["viewpoint"]["kind"] == "sight" and keys["water"]["kind"] == "service"
    assert all(set(c) == {"key", "label", "kind"} for c in body)


async def test_along_route_returns_ranked_pois_and_passes_the_options(client, stub):
    response = await client.post(
        "/v1/pois/along-route",
        json={"coordinates": COORDS, "categories": ["viewpoint"], "buffer_m": 900},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pois"][0]["id"] == "node/1" and body["pois"][0]["fame"] == 7
    assert body["truncated"] is True and body["fame_status"] == "partial"
    kind, line, categories, buffer_m, _ = stub.calls[0]
    assert line == [(10.0, 47.5), (10.1, 47.51), (10.2, 47.5)]
    assert categories == ["viewpoint"] and buffer_m == 900


async def test_the_buffer_is_capped_by_the_server(client, stub):
    await client.post("/v1/pois/along-route", json={"coordinates": COORDS, "buffer_m": 1e6})
    assert stub.calls[0][3] == api_module.settings.poi_max_buffer_m
    await client.post("/v1/pois/along-route", json={"coordinates": COORDS})
    assert stub.calls[1][3] == api_module.settings.poi_default_buffer_m


@pytest.mark.parametrize(
    "body",
    [
        {"coordinates": [[10.0, 47.5]]},
        {"coordinates": [[10.0, 47.5], [200.0, 47.5]]},
        {"coordinates": [[10.0, 47.5], [10.1]]},
        {"coordinates": COORDS, "categories": ["casino"]},
        {"coordinates": COORDS, "buffer_m": 0},
        {"coordinates": COORDS, "limit_per_category": 500},
    ],
)
async def test_bad_along_route_requests_are_rejected_before_any_lookup(client, stub, body):
    response = await client.post("/v1/pois/along-route", json=body)
    assert response.status_code == 422 and stub.calls == []


async def test_a_lookup_failure_is_a_502_not_a_crash(client, monkeypatch):
    monkeypatch.setattr(
        api_module,
        "_poi_service",
        StubService(error=ProviderUnavailableError("Overpass is busy", provider="overpass-poi")),
    )
    response = await client.post("/v1/pois/along-route", json={"coordinates": COORDS})
    assert response.status_code == 502 and "busy" in response.json()["detail"]


async def test_endpoints_are_503_when_pois_are_switched_off(client, monkeypatch):
    monkeypatch.setattr(api_module, "_poi_service", None)
    assert (
        await client.post("/v1/pois/along-route", json={"coordinates": COORDS})
    ).status_code == 503
    assert (await client.get("/v1/pois/in-bbox?bbox=10,47,10.1,47.1")).status_code == 503
    assert (await client.get("/v1/pois/info?wikidata=Q42")).status_code == 503
    assert (await client.get("/v1/capabilities")).json()["pois"] is False


async def test_in_bbox_parses_the_view_and_the_categories(client, stub):
    response = await client.get(
        "/v1/pois/in-bbox?bbox=10,47,10.2,47.1&categories=viewpoint,water&limit_per_category=5"
    )
    assert response.status_code == 200
    assert stub.calls[0] == ("bbox", (10.0, 47.0, 10.2, 47.1), ["viewpoint", "water"], 5)


@pytest.mark.parametrize(
    "query",
    [
        "bbox=nonsense",
        "bbox=10,47,10.1",
        "bbox=10.2,47,10,47.1",  # west of east
        "bbox=10,47,11,47.1",  # a full degree wide
        "bbox=10,47,10.1,47.1&categories=casino",
        "bbox=-200,47,10,47.1",
    ],
)
async def test_bad_bboxes_are_rejected(client, stub, query):
    response = await client.get(f"/v1/pois/in-bbox?{query}")
    assert response.status_code == 422 and stub.calls == []


async def test_info_validates_every_identifier_before_looking_anything_up(client, stub):
    ok = await client.get(
        "/v1/pois/info?wikidata=Q4152&wikipedia=de:Schloss%20Neuschwanstein"
        "&osm_id=way/5&website=https://example.org&lang=DE"
    )
    assert ok.status_code == 200 and ok.json()["title"] == "T"
    assert stub.calls[0][1] == {
        "wikidata": "Q4152",
        "wikipedia": "de:Schloss Neuschwanstein",
        "osm_id": "way/5",
        "website": "https://example.org",
        "lang": "de",
    }
    for query in (
        "wikidata=42",
        "wikipedia=nocolon",
        "osm_id=../etc",
        "lang=evil.com",
        "lang=x/y",
        "lang=en%0A",
        "wikidata=Q42%0A",
    ):
        assert (await client.get(f"/v1/pois/info?{query}")).status_code == 422
    assert len(stub.calls) == 1


async def test_a_non_http_website_is_ignored_not_trusted(client, stub):
    await client.get("/v1/pois/info?wikidata=Q1&website=javascript:alert(1)")
    assert stub.calls[0][1]["website"] is None


# ------------------------------------------------------------- poi_stops in a plan


def test_poi_stops_request_defaults_and_limits():
    request = PoiStopsRequest()
    assert request.count == 2 and request.corridor_km == 5.0 and request.min_fame == 5
    assert "viewpoint" in request.categories and "water" not in request.categories
    for bad in (
        {"count": 0},
        {"count": 6},
        {"corridor_km": 0.1},
        {"corridor_km": 99},
        {"min_fame": 0},
    ):
        with pytest.raises(ValueError):
            PoiStopsRequest(**bad)
    with pytest.raises(ValueError, match="services"):
        PoiStopsRequest(categories=["water"])
    with pytest.raises(ValueError, match="unknown"):
        PoiStopsRequest(categories=["casino"])
    with pytest.raises(ValueError):
        PoiStopsRequest(categories=[])
    with pytest.raises(ValueError):
        PoiStopsRequest(count=1, surprise=True)


def plan(**kw):
    return RoutePlanAPIRequest.model_validate(
        {"origin": "A", "destination": "B", "constraints": {}, **kw}
    )


def test_poi_stops_cannot_combine_with_loops_or_overflow_the_via_limit():
    assert plan(poi_stops={"count": 3}).poi_stops.count == 3
    with pytest.raises(ValueError, match="return_to_origin"):
        RoutePlanAPIRequest.model_validate(
            {
                "origin": "A",
                "constraints": {"return_to_origin": True, "target_distance_km": 30},
                "poi_stops": {},
            }
        )
    vias = [{"lon": 10.0 + i / 100, "lat": 47.5} for i in range(9)]
    with pytest.raises(ValueError, match="via points"):
        plan(via=vias, poi_stops={"count": 2})
    assert plan(via=vias, poi_stops={"count": 1}).poi_stops.count == 1


class ScriptedGraph:
    """Answers the first call with `first` and any later call with `retry`."""

    def __init__(self, first, retry=None):
        self.first, self.retry = first, retry
        self.inputs = []

    async def ainvoke(self, state):
        self.inputs.append(state["raw_input"])
        return self.first if len(self.inputs) == 1 else self.retry


def ready_state(**extra):
    from bike_routing_agent.models import RouteCandidate, RouteMetrics

    candidate = RouteCandidate(
        provider="p",
        provider_profile="x",
        geometry_geojson={"type": "LineString", "coordinates": COORDS},
        metrics=RouteMetrics(distance_m=1000),
        score=0.5,
        rank=1,
    ).model_dump(mode="json")
    return {"status": "ready", "selected_candidate": candidate, "candidates": [candidate], **extra}


STOP = poi("node/9", category="historic", fame=80).model_dump(mode="json")


async def test_the_stops_reach_the_graph_and_come_back_in_the_response(client, monkeypatch):
    graph = ScriptedGraph(ready_state(poi_stops=[STOP], poi_stops_status="ok"))
    monkeypatch.setattr(api_module, "_graph", graph)
    body = (
        await client.post(
            "/v1/route/plan",
            json={"origin": "A", "destination": "B", "poi_stops": {"count": 1}},
        )
    ).json()
    assert graph.inputs[0]["poi_stops"] == {
        "count": 1,
        "categories": graph.inputs[0]["poi_stops"]["categories"],
        "corridor_km": 5.0,
        "min_fame": 5,
    }
    assert body["poi_stops_status"] == "ok" and body["poi_stops"][0]["id"] == "node/9"


async def test_plans_without_stops_do_not_mention_them(client, monkeypatch):
    graph = ScriptedGraph(ready_state())
    monkeypatch.setattr(api_module, "_graph", graph)
    body = (await client.post("/v1/route/plan", json={"origin": "A", "destination": "B"})).json()
    assert graph.inputs[0]["poi_stops"] is None
    assert body["poi_stops"] is None and body["poi_stops_status"] is None


@pytest.mark.parametrize("failure", ["provider_failure", "no_route"])
async def test_when_the_engines_cannot_route_through_the_stops_the_plan_is_made_without(
    client, monkeypatch, failure
):
    graph = ScriptedGraph(
        {"status": failure, "poi_stops": [STOP], "poi_stops_status": "ok", "errors": []},
        retry=ready_state(poi_stops_status="none_found"),
    )
    monkeypatch.setattr(api_module, "_graph", graph)
    body = (
        await client.post(
            "/v1/route/plan", json={"origin": "A", "destination": "B", "poi_stops": {}}
        )
    ).json()
    assert len(graph.inputs) == 2 and graph.inputs[1]["poi_stops"] is None
    assert body["status"] == "ready"
    assert body["poi_stops"] == [] and body["poi_stops_status"] == "dropped"


async def test_if_even_the_plan_without_stops_fails_the_original_failure_is_reported(
    client, monkeypatch
):
    first = {"status": "no_route", "poi_stops": [STOP], "poi_stops_status": "ok", "errors": []}
    graph = ScriptedGraph(first, retry={"status": "no_route", "errors": []})
    monkeypatch.setattr(api_module, "_graph", graph)
    body = (
        await client.post(
            "/v1/route/plan", json={"origin": "A", "destination": "B", "poi_stops": {}}
        )
    ).json()
    assert body["status"] == "no_route" and body["poi_stops_status"] == "ok"


async def test_a_failure_without_stops_is_not_retried(client, monkeypatch):
    graph = ScriptedGraph(
        {"status": "no_route", "poi_stops": [], "poi_stops_status": "none_found", "errors": []}
    )
    monkeypatch.setattr(api_module, "_graph", graph)
    await client.post("/v1/route/plan", json={"origin": "A", "destination": "B", "poi_stops": {}})
    assert len(graph.inputs) == 1
