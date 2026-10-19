"""The requests an optimisation makes of BRouter, and how the adapter runs them."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from bike_routing_agent.models import (
    Coordinate,
    OptimizeGoal,
    OptimizeSpec,
    RouteConstraints,
    RoutingRequest,
)
from bike_routing_agent.providers.brouter import BRouterAdapter
from bike_routing_agent.providers.brouter_optimize import (
    EFFORT_DETOURS,
    EFFORT_REQUESTS,
    detour_stops,
    lead_profile,
    plan_variants,
)

BASE = "http://brouter.test"
URL = f"{BASE}/brouter"


def spec(
    *metrics: str, effort: str = "normal", detours: bool = False, max_detour_pct: float = 30.0
) -> OptimizeSpec:
    """Detours are off unless a test is about them: the other tests count requests."""
    return OptimizeSpec(
        goals=[OptimizeGoal(metric=m) for m in metrics],  # type: ignore[arg-type]
        effort=effort,  # type: ignore[arg-type]
        detours=detours,
        max_detour_pct=max_detour_pct,
    )


def request(bike: str = "gravel", km_east: float = 60) -> RoutingRequest:
    return RoutingRequest(
        origin=Coordinate(lon=10.0, lat=52.0),
        destination=Coordinate(lon=10.0 + km_east / 68.0, lat=52.0),
        constraints=RouteConstraints(bike_type=bike),  # type: ignore[arg-type]
    )


def payload(rows=((1000, "highway=track surface=asphalt"),)) -> dict:
    header = [
        "Longitude", "Latitude", "Elevation", "Distance", "CostPerKm", "ElevCost",
        "TurnCost", "NodeCost", "InitialCost", "WayTags", "NodeTags", "Time", "Energy",
    ]  # fmt: skip
    messages = [header] + [
        ["0", "0", "0", str(d), "0", "0", "0", "0", "0", tags, "", "0", "0"] for d, tags in rows
    ]
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[10.0, 52.0], [10.1, 52.1]]},
                "properties": {
                    "track-length": str(sum(d for d, _ in rows)),
                    "total-time": "600",
                    "filtered ascend": "12",
                    "messages": messages,
                },
            }
        ],
    }


def test_the_profiles_that_tend_to_serve_the_goals_come_first_and_the_own_one_is_left_out():
    traffic = plan_variants(spec("traffic"), "custom_gravel-v2")
    assert [v.profile for v in traffic[:3]] == [
        "custom_commuter-v1", "custom_touring-v1", "trekking",
    ]  # fmt: skip
    assert all(v.profile != "custom_gravel-v2" or v.alternative_index > 0 for v in traffic)
    paved = plan_variants(spec("paved"), "custom_gravel-v2")
    assert [v.profile for v in paved[:2]] == ["trekking", "fastbike"]


def test_the_shortest_route_is_always_among_them_as_the_baseline_for_the_detour():
    for goals in (("traffic",), ("paved",), ("duration",), ("traffic", "paved")):
        assert any(
            v.profile == "shortest" for v in plan_variants(spec(*goals, effort="thorough"), "mtb")
        )


def test_brouters_own_second_and_third_routes_are_asked_for_the_leading_profiles():
    variants = plan_variants(spec("traffic", effort="thorough"), "custom_gravel-v2")
    second = [v for v in variants if v.alternative_index == 1]
    assert {v.profile for v in second} == {"custom_gravel-v2", "custom_commuter-v1"}
    assert any(v.alternative_index == 2 for v in variants)


def test_the_traffic_switch_is_tried_the_other_way_only_when_traffic_is_not_a_goal():
    with_traffic = plan_variants(spec("traffic", "paved", effort="thorough"), "custom_gravel-v2")
    assert not any(v.overrides for v in with_traffic)
    without = plan_variants(spec("duration", effort="thorough"), "custom_gravel-v2")
    switched = [v for v in without if v.overrides]
    assert len(switched) == 1 and set(switched[0].overrides.values()) == {0}
    assert switched[0].source == "traffic not avoided"


@pytest.mark.parametrize("effort", ["quick", "normal", "thorough"])
def test_the_effort_level_cuts_the_list(effort):
    got = plan_variants(spec("traffic", "paved", "duration", effort=effort), "custom_gravel-v2")
    assert len(got) == min(EFFORT_REQUESTS[effort], len(got)) <= EFFORT_REQUESTS[effort]
    assert len({(v.profile, v.alternative_index, tuple(v.overrides.items())) for v in got}) == len(
        got
    )


@respx.mock
async def test_the_adapter_fetches_every_variant_with_its_own_index_and_marks_where_it_came_from():
    calls = []

    def answer(req: httpx.Request) -> httpx.Response:
        calls.append(dict(req.url.params))
        return httpx.Response(200, json=payload())

    respx.get(URL).mock(side_effect=answer)
    adapter = BRouterAdapter(base_url=BASE)
    got = await adapter.optimization_candidates(request(), spec("traffic", "paved"))
    assert len(got) == len(calls) > 3
    assert {c["alternativeidx"] for c in calls} >= {"0", "1", "2"}
    assert {"custom_commuter-v1", "trekking", "shortest"} <= {c["profile"] for c in calls}
    sources = {c.provenance["optimizer_source"] for c in got}
    assert {"profile", "2nd route", "3rd route"} <= sources
    assert all(c.provenance["alternative_of"] == "custom_gravel-v2" for c in got)
    assert all(c.metrics.traffic_index is not None for c in got)  # the tags were read


@respx.mock
async def test_a_variant_that_fails_or_finds_no_route_is_simply_absent():
    def answer(req: httpx.Request) -> httpx.Response:
        if req.url.params["alternativeidx"] != "0":
            return httpx.Response(400, text="no alternative route found")
        if req.url.params["profile"] == "trekking":
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=payload())

    respx.get(URL).mock(side_effect=answer)
    adapter = BRouterAdapter(base_url=BASE, max_retries=0)
    got = await adapter.optimization_candidates(request(), spec("traffic", "paved"))
    assert got and all(c.provider_profile != "trekking" for c in got)
    assert all(c.provenance["optimizer_source"] == "profile" for c in got)


@respx.mock
async def test_variants_that_miss_the_deadline_are_dropped_and_the_rest_returned(monkeypatch):
    async def answer(req: httpx.Request) -> httpx.Response:
        if req.url.params["profile"] == "mtb":
            await asyncio.sleep(30)
        return httpx.Response(200, json=payload())

    respx.get(URL).mock(side_effect=answer)
    adapter = BRouterAdapter(base_url=BASE, optimize_timeout_max_s=0.0)  # the 20 s floor applies
    monkeypatch.setattr(adapter, "optimization_deadline_s", lambda request: 0.5)
    got = await adapter.optimization_candidates(request("mountain"), spec("unpaved"))
    assert got and all(c.provider_profile != "mtb" for c in got)


def test_the_deadline_grows_with_the_trip_up_to_its_maximum():
    adapter = BRouterAdapter(
        base_url=BASE, optimize_timeout_per_km_s=1.0, optimize_timeout_max_s=100.0
    )
    short = adapter.optimization_deadline_s(request(km_east=10))
    long_ = adapter.optimization_deadline_s(request(km_east=60))
    huge = adapter.optimization_deadline_s(request(km_east=900))
    assert short == pytest.approx(30.0, abs=1.0)
    assert long_ == pytest.approx(80.0, abs=1.5)
    assert huge == 100.0


# ------------------------------------------------------------------ routes through places


def poi_via(name: str, along_km: float, lon: float = 10.3, lat: float = 52.01):
    from bike_routing_agent.providers.brouter_optimize import PoiVia

    return PoiVia(name=name, coordinate=Coordinate(lon=lon, lat=lat), along_km=along_km)


def test_places_become_via_sets_each_alone_and_the_best_two_together_in_travel_order():
    from bike_routing_agent.providers.brouter_optimize import poi_via_sets

    vias = [poi_via("Burg", 40.0), poi_via("Kloster", 12.0), poi_via("Turm", 25.0), poi_via("x", 1)]
    sets = poi_via_sets(vias)
    assert [[v.name for v in s] for s in sets] == [
        ["Burg"],
        ["Kloster"],
        ["Turm"],
        ["Kloster", "Burg"],
    ]
    assert poi_via_sets([]) == [] and [[v.name for v in s] for s in poi_via_sets(vias, room=1)] == [
        ["Burg"], ["Kloster"], ["Turm"],
    ]  # fmt: skip


@respx.mock
async def test_routes_through_the_places_are_requested_with_them_as_via_points():
    seen: list[str] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.params["lonlats"])
        return httpx.Response(200, json=payload())

    respx.get(URL).mock(side_effect=answer)
    adapter = BRouterAdapter(base_url=BASE)
    got = await adapter.optimization_candidates(
        request(), spec("pois", effort="quick"), poi_vias=[poi_via("Burg", 20, 10.4, 52.02)]
    )
    through = [c for c in got if c.provenance.get("optimizer_via")]
    assert [c.provenance["optimizer_via"] for c in through] == [["Burg"]]
    assert through[0].provenance["optimizer_source"] == "via Burg"
    assert any("10.4000000,52.0200000" in lonlats for lonlats in seen)  # the place is on the way
    # the quick budget (4) is shared: one route through the place, the rest are profile variants
    assert len(got) <= 4 + 1


@respx.mock
async def test_the_persons_own_via_points_are_never_reordered_by_places():
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload()))
    own = request().model_copy(update={"via": [Coordinate(lon=10.2, lat=52.0)]})
    got = await BRouterAdapter(base_url=BASE).optimization_candidates(
        own, spec("pois", effort="quick"), poi_vias=[poi_via("Burg", 20)]
    )
    assert not any(c.provenance.get("optimizer_via") for c in got)


@respx.mock
async def test_a_place_that_cannot_be_routed_through_is_simply_absent():
    def answer(req: httpx.Request) -> httpx.Response:
        if "10.4000000,52.0200000" in req.url.params["lonlats"]:
            return httpx.Response(400, text="via point not mappable")
        return httpx.Response(200, json=payload())

    respx.get(URL).mock(side_effect=answer)
    got = await BRouterAdapter(base_url=BASE).optimization_candidates(
        request(), spec("pois", effort="quick"), poi_vias=[poi_via("Gipfel", 20, 10.4, 52.02)]
    )
    assert got and not any(c.provenance.get("optimizer_via") for c in got)


# ------------------------------------------------------------------------------- side detours

ORIGIN = Coordinate(lon=10.0, lat=52.0)


def east(km: float) -> Coordinate:
    return Coordinate(lon=10.0 + km / 68.5, lat=52.0)


def straight_km(a: Coordinate, b: Coordinate) -> float:
    import math

    dx = (b.lon - a.lon) * math.cos(math.radians((a.lat + b.lat) / 2)) * 111.32
    return math.hypot(dx, (b.lat - a.lat) * 111.32)


def test_detours_lie_on_both_sides_of_the_direct_line_and_are_labelled():
    found = detour_stops(ORIGIN, east(60), max_detour_pct=30, count=6)
    assert [d.side for d in found[:2]] == ["left", "right"]
    # Travelling east, left is north (higher latitude).
    north = [d for d in found if d.side == "left"]
    south = [d for d in found if d.side == "right"]
    assert all(d.via.lat > 52.0 for d in north) and all(d.via.lat < 52.0 for d in south)
    assert found[0].label == "detour left 50 %" and found[1].label == "detour right 50 %"
    assert len(found) == 6 and len({d.label for d in found}) == 6


def test_travelling_the_other_way_swaps_what_left_and_right_mean():
    forward = detour_stops(ORIGIN, east(60), max_detour_pct=30, count=2)
    backward = detour_stops(east(60), ORIGIN, max_detour_pct=30, count=2)
    assert forward[0].side == backward[0].side == "left"
    assert forward[0].via.lat > 52.0 > backward[0].via.lat  # the same word, the other bank


@pytest.mark.parametrize("pct", [5.0, 15.0, 30.0, 60.0])
def test_a_detour_is_sized_from_the_allowed_detour_and_never_exceeds_it(pct):
    direct = straight_km(ORIGIN, east(60))
    for d in detour_stops(ORIGIN, east(60), max_detour_pct=pct, count=6):
        way = straight_km(ORIGIN, d.via) + straight_km(d.via, east(60))
        assert 1.0 < way / direct <= 1 + pct / 100.0  # the road adds more, hence the margin


def test_a_larger_allowance_sends_the_via_point_further_out():
    near = detour_stops(ORIGIN, east(60), max_detour_pct=10, count=1)[0]
    far = detour_stops(ORIGIN, east(60), max_detour_pct=40, count=1)[0]
    assert far.via.lat - 52.0 > near.via.lat - 52.0 > 0


def test_there_are_no_detours_when_none_are_wanted_or_the_trip_is_tiny():
    assert detour_stops(ORIGIN, east(60), max_detour_pct=0, count=4) == []
    assert detour_stops(ORIGIN, east(60), max_detour_pct=30, count=0) == []
    assert detour_stops(ORIGIN, east(2), max_detour_pct=30, count=4) == []  # a street, not a route
    assert detour_stops(ORIGIN, ORIGIN, max_detour_pct=30, count=4) == []


def test_the_via_point_stays_a_sensible_distance_out_whatever_the_allowance():
    direct = straight_km(ORIGIN, east(60))
    for d in detour_stops(ORIGIN, east(60), max_detour_pct=300, count=6):
        out = abs(d.via.lat - 52.0) * 111.32
        assert 0.3 <= out <= 0.36 * direct


def test_the_detour_is_routed_with_the_profile_that_serves_the_leading_goal():
    assert lead_profile(spec("traffic"), "custom_gravel-v2") == "custom_commuter-v1"
    assert lead_profile(spec("paved"), "custom_gravel-v2") == "trekking"
    heavy = OptimizeSpec(
        goals=[OptimizeGoal(metric="traffic", weight=0.3), OptimizeGoal(metric="paved", weight=1.0)]
    )
    assert lead_profile(heavy, "custom_gravel-v2") == "trekking"  # the weightier goal leads
    assert lead_profile(spec("distance"), "mtb") == "fastbike"


@respx.mock
async def test_the_adapter_routes_through_each_detour_and_marks_it_in_the_provenance():
    calls = []

    def answer(req: httpx.Request) -> httpx.Response:
        calls.append(dict(req.url.params))
        return httpx.Response(200, json=payload())

    respx.get(URL).mock(side_effect=answer)
    adapter = BRouterAdapter(base_url=BASE)
    found = await adapter.optimization_candidates(request(), spec("traffic", detours=True))
    detoured = [c for c in found if "optimizer_detour" in c.provenance]
    assert len(detoured) == EFFORT_DETOURS["normal"]
    assert {c.provenance["optimizer_source"] for c in detoured} == {
        "detour left 50 %", "detour right 50 %", "detour left 35 %", "detour right 65 %",
    }  # fmt: skip
    one = detoured[0].provenance
    assert one["alternative_of"] == "custom_gravel-v2" and len(one["optimizer_detour"]["via"]) == 2
    via_calls = [c for c in calls if c["lonlats"].count("|") == 2]  # origin | via | destination
    assert len(via_calls) == EFFORT_DETOURS["normal"]
    assert {c["profile"] for c in via_calls} == {"custom_commuter-v1"}  # the traffic profile


@respx.mock
async def test_detours_are_not_tried_unless_wanted_and_never_for_loops_own_vias_or_speed_goals():
    respx.get(URL).mock(return_value=httpx.Response(200, json=payload()))
    adapter = BRouterAdapter(base_url=BASE)

    async def detoured(req: RoutingRequest, wanted: OptimizeSpec) -> int:
        found = await adapter.optimization_candidates(req, wanted)
        return sum("optimizer_detour" in c.provenance for c in found)

    assert await detoured(request(), spec("traffic", detours=True)) > 0
    assert await detoured(request(), spec("traffic", detours=False)) == 0
    assert await detoured(request(), spec("traffic")) == 0  # the default
    assert await detoured(request(), spec("distance", "duration", detours=True)) == 0
    own_via = request().model_copy(update={"via": [east(30)]})
    assert await detoured(own_via, spec("traffic", detours=True)) == 0
    loop = request().model_copy(
        update={"constraints": RouteConstraints(return_to_origin=True, target_distance_km=40)}
    )
    assert await detoured(loop, spec("traffic", detours=True)) == 0


@respx.mock
async def test_a_detour_that_fails_is_simply_absent():
    def answer(req: httpx.Request) -> httpx.Response:
        if req.url.params["lonlats"].count("|") == 2:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=payload())

    respx.get(URL).mock(side_effect=answer)
    found = await BRouterAdapter(base_url=BASE).optimization_candidates(
        request(), spec("traffic", detours=True)
    )
    assert found and not any("optimizer_detour" in c.provenance for c in found)


def test_detours_are_off_by_default_in_the_spec_and_can_be_switched_on():
    assert OptimizeSpec(goals=[OptimizeGoal(metric="traffic")]).detours is False
    assert OptimizeSpec(goals=[OptimizeGoal(metric="traffic")], detours=True).detours is True


def test_a_trip_across_the_antimeridian_gets_its_detours_on_the_short_side():
    start, end = Coordinate(lon=179.9, lat=52.0), Coordinate(lon=-179.9, lat=52.0)
    found = detour_stops(start, end, max_detour_pct=30, count=6)
    assert found  # the 13 km crossing, not a 358 degree trip the other way round
    for d in found:
        assert -180 <= d.via.lon < 180 and abs(d.via.lat - 52.0) < 0.2
        east_of_start = (d.via.lon - start.lon) % 360
        assert east_of_start < 1.0 or east_of_start > 359.0  # near the crossing


@respx.mock
async def test_detours_queue_behind_the_profile_variants_and_never_ahead_of_them():
    calls = []

    def answer(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.params["lonlats"].count("|") == 2)  # True: a detour (has a via)
        return httpx.Response(200, json=payload())

    respx.get(URL).mock(side_effect=answer)
    adapter = BRouterAdapter(base_url=BASE, max_concurrency=1)
    await adapter.optimization_candidates(request(), spec("traffic", detours=True))
    assert calls.count(True) == EFFORT_DETOURS["normal"]
    first_detour = calls.index(True)
    assert not any(calls[:first_detour]) and all(calls[first_detour:])  # detours come last
