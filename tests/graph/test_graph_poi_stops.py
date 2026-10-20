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


# ------------------------------------------------------------------------------------- loops


class RingPois:
    """A sight at the middle of the first edge of whatever circuit it is asked to search along."""

    def __init__(self, distance_m=80.0):
        self.lines: list = []
        self.distance_m = distance_m

    async def along_route(self, line, categories, *, buffer_m, linked_only):
        self.lines.append((list(line), buffer_m))
        (lon0, lat0), (lon1, lat1) = line[0], line[1]
        edge_km = ((lon1 - lon0) ** 2 * 0.6 + (lat1 - lat0) ** 2) ** 0.5 * 111.32
        castle = Poi(
            id="way/2",
            name="Ringburg",
            category="historic",
            kind="sight",
            lon=(lon0 + lon1) / 2,
            lat=(lat0 + lat1) / 2,
            fame=80,
            along_route_km=edge_km / 2,
            distance_from_route_m=self.distance_m,
        )
        return PoiSearchResult([castle], truncated=False, fame_status="ok")


def loop_request(**extra):
    return {
        "raw_input": {
            "origin": "A",
            "constraints": {
                "bike_type": "gravel",
                "return_to_origin": True,
                "target_distance_km": 30,
            },
            **extra,
        }
    }


async def test_a_loop_is_routed_through_a_sight_on_a_circuit_the_stops_node_drew(tmp_path):
    router, pois = RecordingRouter(), RingPois()
    graph = build_graph(
        geocode_provider=geocoder(),
        routing_providers=[router],
        export_dir=tmp_path,
        poi_service=pois,
    )
    result = await graph.ainvoke(loop_request(poi_stops={"count": 1, "categories": ["historic"]}))
    assert result["status"] == "ready" and result["poi_stops_status"] == "ok"
    request_made = router.requests[0]
    assert request_made.destination == request_made.origin  # still a loop
    assert len(request_made.via) == 3  # the two corners of the circuit and the sight
    assert any((v.lon, v.lat) == (result["poi_stops"][0]["lon"], result["poi_stops"][0]["lat"])
               for v in request_made.via)  # fmt: skip
    line, buffer_m = pois.lines[0]
    assert line[0] == line[-1] and buffer_m == 2000  # searched along the circuit, narrowly
    # the plan of the circuit the stops node drew reaches the explanation
    assert result["loop_plan"] is not None and result["loop_plan"]["radius_m"] > 0
    assert "waypoints were synthesized" in result["explanation"]
    assert result["poi_stops"][0]["name"] == "Ringburg"  # the chat and the page name the stops


async def test_a_loop_without_sights_is_planned_exactly_as_before(tmp_path):
    router = RecordingRouter()

    class Nothing:
        async def along_route(self, line, categories, *, buffer_m, linked_only):
            return PoiSearchResult([], truncated=False, fame_status="ok")

    graph = build_graph(
        geocode_provider=geocoder(),
        routing_providers=[router],
        export_dir=tmp_path,
        poi_service=Nothing(),
    )
    result = await graph.ainvoke(loop_request(poi_stops={"count": 2, "categories": ["historic"]}))
    assert result["status"] == "ready" and result["poi_stops_status"] == "none_found"
    assert len(router.requests[0].via) == 2  # only the synthesized corners
    assert result["loop_plan"]["radius_m"] > 0  # drawn by the route node, not the stops node
