import pytest

from bike_routing_agent.errors import ProviderUnavailableError
from bike_routing_agent.nodes.poi_stops import build_poi_stops_node
from bike_routing_agent.poi.models import Poi
from bike_routing_agent.poi.service import PoiSearchResult

ORIGIN = {"lon": 10.0, "lat": 47.5}
DESTINATION = {"lon": 10.3, "lat": 47.5}  # about 22.5 km east


def sight(ident, lon, km, fame, *, lat=47.5005):
    return Poi(
        id=ident,
        category="historic",
        kind="sight",
        lon=lon,
        lat=lat,
        fame=fame,
        along_route_km=km,
        distance_from_route_m=60.0,
    )


class FakeService:
    def __init__(self, pois=None, *, error=None, fame_status="ok"):
        self.pois = pois or []
        self.error = error
        self.fame_status = fame_status
        self.calls = []

    async def along_route(self, line, categories, *, buffer_m, linked_only):
        self.calls.append((line, categories, buffer_m, linked_only))
        if self.error:
            raise self.error
        return PoiSearchResult(self.pois, truncated=False, fame_status=self.fame_status)


def state(**kw):
    base = {
        "poi_stops_request": {"count": 2, "categories": ["historic"], "corridor_km": 4},
        "constraints": {},
        "resolved_origin": ORIGIN,
        "resolved_destination": DESTINATION,
        "resolved_via": [],
    }
    base.update(kw)
    return base


async def run(service, **kw):
    return await build_poi_stops_node(service=service)(state(**kw))


async def test_without_a_request_the_node_does_nothing():
    assert await run(FakeService(), poi_stops_request=None) == {}


async def test_the_most_famous_sights_become_via_points_in_travel_order():
    service = FakeService(
        [
            sight("a", 10.05, 3.8, 10),
            sight("b", 10.15, 11.3, 90),
            sight("c", 10.22, 16.5, 70),
        ]
    )
    update = await run(service)
    assert update["poi_stops_status"] == "ok"
    assert [p["id"] for p in update["poi_stops"]] == ["b", "c"]
    assert update["resolved_via"] == [
        {"lon": 10.15, "lat": 47.5005},
        {"lon": 10.22, "lat": 47.5005},
    ]
    line, categories, buffer_m, linked_only = service.calls[0]
    assert line == [(10.0, 47.5), (10.3, 47.5)]
    assert categories == ["historic"] and buffer_m == 4000 and linked_only is True


async def test_the_callers_own_via_points_are_kept_and_ordered_with_the_stops():
    user_via = {"lon": 10.2, "lat": 47.5}
    service = FakeService([sight("b", 10.1, 7.6, 90), sight("c", 10.25, 18.8, 80)])
    update = await run(service, resolved_via=[user_via])
    assert update["resolved_via"] == [
        {"lon": 10.1, "lat": 47.5005},
        user_via,
        {"lon": 10.25, "lat": 47.5005},
    ]
    # The line the stops lie along passes through the caller's via point.
    assert service.calls[0][0] == [(10.0, 47.5), (10.2, 47.5), (10.3, 47.5)]


async def test_obscure_sights_are_not_presented_as_famous():
    service = FakeService([sight("a", 10.1, 7.6, 3), sight("b", 10.2, 15.0, 6)])
    update = await run(
        service,
        poi_stops_request={"count": 2, "categories": ["historic"], "min_fame": 5},
    )
    assert [p["id"] for p in update["poi_stops"]] == ["b"]
    strict = await run(
        service,
        poi_stops_request={"count": 2, "categories": ["historic"], "min_fame": 50},
    )
    assert strict == {"poi_stops": [], "poi_stops_status": "none_found"}


async def test_stops_respect_the_via_limit():
    vias = [{"lon": 10.0 + i * 0.001, "lat": 47.5} for i in range(9)]  # 9 of 10 used
    service = FakeService([sight("b", 10.1, 7.6, 90), sight("c", 10.2, 15.0, 80)])
    update = await run(service, resolved_via=vias)
    assert [p["id"] for p in update["poi_stops"]] == ["b"]
    assert len(update["resolved_via"]) == 10
    full = await run(FakeService(), resolved_via=vias + [{"lon": 10.01, "lat": 47.5}])
    assert full == {"poi_stops": [], "poi_stops_status": "none_found"}


@pytest.mark.parametrize(
    ("service", "status"),
    [
        (FakeService(error=ProviderUnavailableError("down", provider="overpass")), "unavailable"),
        (FakeService([sight("b", 10.1, 7.6, None)], fame_status="unavailable"), "unavailable"),
        (FakeService([]), "none_found"),
        (FakeService([sight("b", 10.1, 7.6, None)]), "none_found"),  # fame unknown: not "famous"
        (None, "unavailable"),
    ],
)
async def test_failures_leave_the_route_untouched_and_say_why(service, status):
    update = await run(service)
    assert update == {"poi_stops": [], "poi_stops_status": status}
    assert "resolved_via" not in update


async def test_loops_are_not_supported():
    update = await run(FakeService(), constraints={"return_to_origin": True})
    assert update == {"poi_stops": [], "poi_stops_status": "unsupported"}


async def test_identical_origin_and_destination_has_no_corridor():
    update = await run(FakeService(), resolved_destination=ORIGIN)
    assert update["poi_stops_status"] == "none_found"
