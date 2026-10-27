"""A loop is sized against a real route: the road distance, not the chords, meets the target."""

from __future__ import annotations

import math

import pytest

from bike_routing_agent.errors import ProviderCoverageError, ProviderError, ProviderNoRouteError
from bike_routing_agent.loops import (
    LOOP_BEARINGS_DEG,
    LOOP_MAX_ROUTES,
    Coordinate,
    next_chord_target,
    synthesize_loop_vias,
)
from bike_routing_agent.models import RouteCandidate, RouteMetrics
from bike_routing_agent.nodes.route import build_route_node

ORIGIN = Coordinate(lon=10.5, lat=52.3)


def chord_length_m(request) -> float:
    """The straight-line length of the circuit a request asks for."""
    points = [request.origin, *request.via, request.destination]
    total = 0.0
    for a, b in zip(points, points[1:], strict=False):
        dy = (b.lat - a.lat) * 111_320.0
        dx = (b.lon - a.lon) * 111_320.0 * math.cos(math.radians(a.lat))
        total += math.hypot(dx, dy)
    return total


class WindingRoads:
    """Roads that are ``factor`` times longer than the straight line; can fail on demand."""

    name = "winding"

    def __init__(self, factor: float = 1.4, fail_first: int = 0, error: Exception | None = None):
        self.factor, self.fail_first, self.error = factor, fail_first, error
        self.requests = []

    async def route(self, request):
        self.requests.append(request)
        if self.fail_first > 0:
            self.fail_first -= 1
            raise self.error or ProviderError("target island", provider="winding")
        return RouteCandidate(
            provider="winding",
            provider_profile="p",
            geometry_geojson={"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            metrics=RouteMetrics(distance_m=self.factor * chord_length_m(request), duration_s=1),
        )


def loop_state(km: float = 40) -> dict:
    return {
        "resolved_origin": {"lon": ORIGIN.lon, "lat": ORIGIN.lat},
        "resolved_destination": {"lon": ORIGIN.lon, "lat": ORIGIN.lat},
        "resolved_via": [],
        "constraints": {"return_to_origin": True, "target_distance_km": km},
    }


# ------------------------------------------------------------------------------ the rule


def test_the_next_chord_target_scales_by_goal_over_routed():
    assert next_chord_target(40_000, 56_000, 40_000) == pytest.approx(28_571, abs=1)
    assert next_chord_target(40_000, 30_000, 40_000) == pytest.approx(53_333, abs=1)


def test_one_odd_route_cannot_send_the_next_one_far_off():
    assert next_chord_target(40_000, 400_000, 40_000) == 16_000  # 0.4 x the goal
    assert next_chord_target(40_000, 4_000, 40_000) == 64_000  # 1.6 x the goal


def test_a_loop_can_be_turned_around_the_start_without_changing_its_size():
    plain = synthesize_loop_vias(ORIGIN, 30_000)
    turned = synthesize_loop_vias(ORIGIN, 30_000, bearing_deg=120.0)
    assert turned.vias != plain.vias and turned.bearing_deg == 120.0
    assert turned.reach_m == pytest.approx(plain.reach_m)
    assert synthesize_loop_vias(ORIGIN, 30_000, bearing_deg=360.0).vias[0].lat == pytest.approx(
        plain.vias[0].lat, abs=1e-6
    )
    full = chord_length_m(_request(turned))
    assert full == pytest.approx(30_000, rel=0.02)  # the circuit is still the asked-for chords


def _request(plan):
    from bike_routing_agent.models import RouteConstraints, RoutingRequest

    return RoutingRequest(
        origin=ORIGIN,
        destination=ORIGIN,
        via=list(plan.vias),
        constraints=RouteConstraints(return_to_origin=True, target_distance_km=30),
    )


# ------------------------------------------------------------------------------ in the node


async def test_the_road_distance_lands_near_the_target_not_the_chords():
    roads = WindingRoads(factor=1.4)
    update = await build_route_node(routing_providers=[roads])(loop_state(40))
    routed = update["candidates"][0]["metrics"]["distance_m"]
    assert routed == pytest.approx(40_000, rel=0.10)  # without sizing: 56 000
    plan = update["loop_plan"]
    assert plan["routed_km"] == pytest.approx(40.0, rel=0.10) and plan["attempts"] == 2


async def test_roads_that_already_fit_are_routed_once_for_sizing():
    roads = WindingRoads(factor=1.0)
    update = await build_route_node(routing_providers=[roads])(loop_state(40))
    assert update["loop_plan"]["attempts"] == 1
    assert len(roads.requests) == 2  # one to size it, one for the plan itself


async def test_it_stops_after_the_budget_and_keeps_the_closest_loop():
    class Wild(WindingRoads):
        async def route(self, request):  # whatever is asked, the road comes out 3x as long
            self.requests.append(request)
            return RouteCandidate(
                provider="winding",
                provider_profile="p",
                geometry_geojson={"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
                metrics=RouteMetrics(distance_m=120_000, duration_s=1),
            )

    wild = Wild()
    update = await build_route_node(routing_providers=[wild])(loop_state(40))
    assert len(wild.requests) == LOOP_MAX_ROUTES + 1  # the sizing tries, then the plan
    assert update["status"] == "in_progress"


async def test_a_waypoint_the_engine_cannot_reach_turns_the_loop_around():
    roads = WindingRoads(factor=1.0, fail_first=1)  # "target island detected" the first time
    update = await build_route_node(routing_providers=[roads])(loop_state(40))
    assert update["status"] == "in_progress"
    assert update["loop_plan"]["bearing_deg"] == LOOP_BEARINGS_DEG[1]
    assert roads.requests[0].via != roads.requests[1].via


async def test_a_loop_that_cannot_be_routed_reports_the_engines_own_error():
    roads = WindingRoads(fail_first=99, error=ProviderNoRouteError("no path", provider="winding"))
    update = await build_route_node(routing_providers=[roads])(loop_state(40))
    assert update["status"] == "no_route" and update["errors"][0]["code"] == "no_route"
    assert len(roads.requests) == LOOP_MAX_ROUTES + 1  # tried, then the ordinary request


async def test_missing_map_data_is_not_retried_in_other_directions():
    error = ProviderCoverageError("no tiles", provider="winding", detail={"segments": ["E10_N50"]})
    roads = WindingRoads(fail_first=99, error=error)
    update = await build_route_node(routing_providers=[roads])(loop_state(40))
    assert update["status"] == "provider_failure"
    assert len(roads.requests) == 2  # one sizing attempt, then the ordinary request


async def test_a_loop_drawn_by_the_caller_is_not_resized():
    roads = WindingRoads()
    state = loop_state(40)
    state["resolved_via"] = [{"lon": 10.55, "lat": 52.35}, {"lon": 10.6, "lat": 52.3}]
    update = await build_route_node(routing_providers=[roads])(state)
    assert len(roads.requests) == 1 and update["loop_plan"] is None


async def test_an_ordinary_trip_is_not_sized_at_all():
    roads = WindingRoads()
    state = {
        "resolved_origin": {"lon": 10.5, "lat": 52.3},
        "resolved_destination": {"lon": 10.6, "lat": 52.4},
        "resolved_via": [],
        "constraints": {"bike_type": "road"},
    }
    await build_route_node(routing_providers=[roads])(state)
    assert len(roads.requests) == 1
