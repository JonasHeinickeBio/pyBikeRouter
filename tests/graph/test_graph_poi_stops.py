"""The graph routes through famous POIs when asked, and only then."""

from bike_routing_agent.graph import build_graph
from bike_routing_agent.poi.models import Poi
from bike_routing_agent.poi.service import PoiSearchResult

from .test_graph import FakeGeocoder, FakeRouter, sample_candidate, unambiguous


class RecordingRouter(FakeRouter):
    def __init__(self):
        super().__init__(candidate=sample_candidate())
        self.requests = []

    async def route(self, request):
        self.requests.append(request)
        return await super().route(request)


class StubPois:
    def __init__(self):
        self.calls = 0

    async def along_route(self, line, categories, *, buffer_m, linked_only):
        self.calls += 1
        castle = Poi(
            id="way/1",
            name="Burg",
            category="historic",
            kind="sight",
            lon=10.50,
            lat=52.25,
            fame=70,
            along_route_km=3.0,
            distance_from_route_m=40.0,
        )
        return PoiSearchResult([castle], truncated=False, fame_status="ok")


def geocoder():
    return FakeGeocoder(
        by_query={
            "A": unambiguous("A", 10.40, 52.30),
            "B": unambiguous("B", 10.62, 52.20),
        }
    )


def request(**extra):
    return {
        "raw_input": {
            "origin": "A",
            "destination": "B",
            "constraints": {"bike_type": "gravel"},
            **extra,
        }
    }


async def test_a_famous_poi_becomes_a_via_point_of_the_routing_request(tmp_path):
    router, pois = RecordingRouter(), StubPois()
    graph = build_graph(
        geocode_provider=geocoder(),
        routing_providers=[router],
        export_dir=tmp_path,
        poi_service=pois,
    )
    result = await graph.ainvoke(request(poi_stops={"count": 1, "categories": ["historic"]}))
    assert result["status"] == "ready"
    assert [(v.lon, v.lat) for v in router.requests[0].via] == [(10.50, 52.25)]
    assert result["poi_stops_status"] == "ok" and result["poi_stops"][0]["name"] == "Burg"


async def test_without_a_request_pois_are_never_looked_up(tmp_path):
    router, pois = RecordingRouter(), StubPois()
    graph = build_graph(
        geocode_provider=geocoder(),
        routing_providers=[router],
        export_dir=tmp_path,
        poi_service=pois,
    )
    result = await graph.ainvoke(request())
    assert result["status"] == "ready" and pois.calls == 0
    assert router.requests[0].via == [] and result.get("poi_stops_status") is None


async def test_with_pois_unavailable_the_plan_still_succeeds(tmp_path):
    router = RecordingRouter()
    graph = build_graph(
        geocode_provider=geocoder(), routing_providers=[router], export_dir=tmp_path
    )
    result = await graph.ainvoke(request(poi_stops={"count": 1}))
    assert result["status"] == "ready" and result["poi_stops_status"] == "unavailable"
    assert router.requests[0].via == []
