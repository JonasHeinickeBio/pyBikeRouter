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
from bike_routing_agent.providers.brouter_optimize import EFFORT_REQUESTS, plan_variants

BASE = "http://brouter.test"
URL = f"{BASE}/brouter"


def spec(*metrics: str, effort: str = "normal") -> OptimizeSpec:
    return OptimizeSpec(goals=[OptimizeGoal(metric=m) for m in metrics], effort=effort)  # type: ignore[arg-type]


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
