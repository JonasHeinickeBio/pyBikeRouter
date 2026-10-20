import math

import pytest

from bike_routing_agent.errors import ProviderUnavailableError
from bike_routing_agent.loops import synthesize_loop_vias
from bike_routing_agent.models import Coordinate
from bike_routing_agent.nodes.poi_stops import build_poi_stops_node
from bike_routing_agent.poi.geo import cumulative_lengths_m
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


# ------------------------------------------------------------------------------------ loops

LOOP = {"return_to_origin": True, "target_distance_km": 30.0}


def loop_state(**kw):
    """A loop request: no destination, a size, and the sight search narrowed to the circuit."""
    constraints = {**LOOP, **kw.pop("constraints", {})}
    return state(constraints=constraints, resolved_destination=ORIGIN, **kw)


async def run_loop(service, **kw):
    return await build_poi_stops_node(service=service)(loop_state(**kw))


def ring_for(target_m, direction="clockwise"):
    plan = synthesize_loop_vias(Coordinate(**ORIGIN), target_m, direction=direction)
    return [v.model_dump(mode="json") for v in plan.vias], plan


def ring_line(vias):
    return [(ORIGIN["lon"], ORIGIN["lat"]), *((v["lon"], v["lat"]) for v in vias), (10.0, 47.5)]


def edge_sights(vias, fames, **kw):
    """One sight at the middle of each edge of the circuit, with its position along it."""
    line = ring_line(vias)
    cumulative = cumulative_lengths_m(line)
    sights = []
    for i, fame in enumerate(fames):
        lon = (line[i][0] + line[i + 1][0]) / 2
        lat = (line[i][1] + line[i + 1][1]) / 2
        km = (cumulative[i] + cumulative[i + 1]) / 2 / 1000
        sights.append(sight(f"e{i}", lon, km, fame, lat=lat, **kw))
    return sights


def circle_vias(count, radius_km=5.0):
    """A drawn loop: ``count`` vias spread on a circle through the origin's neighbourhood."""
    out = []
    for i in range(count):
        angle = 2 * math.pi * (i + 1) / (count + 1)
        out.append(
            {
                "lon": 10.0
                + radius_km * (math.cos(angle) - 1) / (111.32 * math.cos(math.radians(47.5))),
                "lat": 47.5 + radius_km * math.sin(angle) / 111.32,
            }
        )
    return out


async def test_a_loop_is_searched_along_its_own_circuit_in_a_narrow_corridor():
    service = FakeService([])
    update = await run_loop(service)
    assert update == {"poi_stops": [], "poi_stops_status": "none_found"}
    line, categories, buffer_m, linked_only = service.calls[0]
    vias, _ = ring_for(30_000)
    assert line == ring_line(vias)
    assert buffer_m == 2000  # the request said 4 km: a stop is a detour out and back
    assert categories == ["historic"] and linked_only is True


async def test_the_stops_join_the_circuit_in_loop_order_and_the_circuit_is_redrawn_smaller():
    vias, plan = ring_for(30_000)
    first, second, _ = edge_sights(vias, [80, 90, 10])  # the third edge is not asked for
    update = await run_loop(FakeService([second, first]))  # found in any order
    assert update["poi_stops_status"] == "ok"
    assert [p["id"] for p in update["poi_stops"]] == ["e0", "e1"]  # visiting order
    # two stops, 60 m off the circuit each: the circuit is drawn for 30 km - 240 m
    smaller, smaller_plan = ring_for(30_000 - 240)
    assert update["loop_plan"] == smaller_plan.to_state()
    assert update["loop_plan"]["radius_m"] < plan.radius_m
    stop = [{"lon": s.lon, "lat": s.lat} for s in (first, second)]
    # edge 0 (origin -> corner 1) holds the first stop, edge 1 the second: s0, v1, s1, v2
    assert update["resolved_via"] == [stop[0], smaller[0], stop[1], smaller[1]]


async def test_a_circuit_never_shrinks_below_seventy_percent_for_its_stops():
    vias, _ = ring_for(30_000)
    sights = edge_sights(
        vias,
        [90, 80],
    )
    for sight_ in sights:
        sight_.distance_from_route_m = 6000.0  # a long way off the circuit, twice
    update = await run_loop(FakeService(sights))
    assert update["poi_stops_status"] == "ok" and len(update["poi_stops"]) == 2
    # 24 km of detours would leave 6 km: the circuit keeps 70 % of its size
    assert update["loop_plan"] == ring_for(21_000)[1].to_state()


async def test_a_loop_the_caller_drew_is_kept_and_only_gets_the_stops_inserted():
    drawn = circle_vias(3)
    service = FakeService(edge_sights(drawn, [90]))
    update = await run_loop(service, resolved_via=drawn)
    assert update["poi_stops_status"] == "ok"
    assert "loop_plan" not in update  # the shape is theirs: nothing is redrawn
    assert all(v in update["resolved_via"] for v in drawn) and len(update["resolved_via"]) == 4
    line = service.calls[0][0]
    assert line == ring_line(drawn)  # searched along their circuit, through their vias


async def test_a_counterclockwise_loop_keeps_its_direction():
    vias, _ = ring_for(30_000, "counterclockwise")
    update = await run_loop(
        FakeService(edge_sights(vias, [90])),
        constraints={"loop_direction": "counterclockwise"},
    )
    assert update["loop_plan"]["direction"] == "counterclockwise"
    assert update["loop_plan"] == ring_for(30_000 - 120, "counterclockwise")[1].to_state()


async def test_loop_stops_respect_the_via_limit_with_the_vias_of_the_circuit():
    drawn = circle_vias(9)  # a drawn loop with 9 of the 10 via points
    service = FakeService(edge_sights(drawn, [90, 80, 70]))
    one = await run_loop(service, resolved_via=drawn)
    assert len(one["poi_stops"]) == 1 and len(one["resolved_via"]) == 10
    full = await run_loop(service, resolved_via=circle_vias(10))
    assert full == {"poi_stops": [], "poi_stops_status": "none_found"}


@pytest.mark.parametrize(
    ("service", "status"),
    [
        (FakeService(error=ProviderUnavailableError("down", provider="overpass")), "unavailable"),
        (FakeService(fame_status="unavailable"), "unavailable"),
        (FakeService([]), "none_found"),
        (None, "unavailable"),
    ],
)
async def test_a_loops_failures_leave_the_circuit_to_the_route_node(service, status):
    update = await run_loop(service)
    assert update == {"poi_stops": [], "poi_stops_status": status}
    assert "loop_plan" not in update and "resolved_via" not in update


async def test_a_loop_that_cannot_be_drawn_is_unsupported_not_guessed():
    polar = await build_poi_stops_node(service=FakeService())(
        loop_state(resolved_origin={"lon": 10.0, "lat": 89.9})
    )
    assert polar == {"poi_stops": [], "poi_stops_status": "unsupported"}
    no_size = await build_poi_stops_node(service=FakeService())(
        state(constraints={"return_to_origin": True}, resolved_destination=ORIGIN)
    )
    assert no_size == {"poi_stops": [], "poi_stops_status": "unsupported"}


async def test_identical_origin_and_destination_has_no_corridor():
    update = await run(FakeService(), resolved_destination=ORIGIN)
    assert update["poi_stops_status"] == "none_found"
