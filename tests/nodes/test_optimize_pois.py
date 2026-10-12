"""The optimise node counts the places near every candidate from one search."""

from __future__ import annotations

from typing import Any

import pytest

from bike_routing_agent.errors import ProviderUnavailableError
from bike_routing_agent.models import OptimizeSpec, RouteCandidate, RouteMetrics
from bike_routing_agent.nodes.optimize import build_optimize_node
from bike_routing_agent.poi.models import Poi
from bike_routing_agent.poi.service import FameStatus, RoutesPoiResult


def candidate(profile: str, km: float, lat: float) -> dict[str, Any]:
    return RouteCandidate(
        provider="brouter",
        provider_profile=profile,
        geometry_geojson={"type": "LineString", "coordinates": [[10.0, lat], [10.3, lat]]},
        metrics=RouteMetrics(distance_m=km * 1000, duration_s=km * 200, ascent_m=50.0),
    ).model_dump(mode="json")


def sight(name: str, fame: int | None) -> Poi:
    return Poi(
        id=f"node/{name}",
        name=name,
        category="historic",
        kind="sight",
        lon=10.1,
        lat=47.5,
        fame=fame,
    )


class StubPois:
    def __init__(self, per_route=None, error: Exception | None = None):
        self.per_route, self.error, self.calls = per_route or [], error, []

    async def along_routes(self, lines, categories, *, buffer_m, linked_only=False, **_):
        self.calls.append((len(lines), list(categories), buffer_m, linked_only))
        if self.error:
            raise self.error
        status: FameStatus = "ok"
        return RoutesPoiResult(per_route=self.per_route, truncated=False, fame_status=status)


def state(**extra):
    return {
        "optimize": {"goals": [{"metric": "pois", "weight": 1.0}], "poi": {"min_fame": 5}},
        "candidates": [
            candidate("a", 50, 47.5),
            candidate("b", 52, 47.52),
            candidate("c", 51, 47.54),
        ],
        "constraints": {},
        **extra,
    }


async def test_the_places_near_each_route_are_counted_once_and_the_names_kept():
    stub = StubPois(
        [
            [sight("Burg", 20)],
            [
                sight("Burg", 20),
                sight("Kloster", 12),
                sight("Kapelle", 2),
                sight("Unmeasured", None),
            ],
            [sight("Burg", 20), sight("Kloster", 12)],
        ]
    )
    update = await build_optimize_node(poi_service=stub)(state())  # type: ignore[arg-type]
    assert stub.calls == [
        (3, list(OptimizeSpec.model_validate(state()["optimize"]).poi.categories), 300.0, True)
    ]
    best = update["selected_candidate"]
    # b and c both pass two places (fame >= 5 only; the unmeasured one does not count): the tie
    # goes to the shorter c
    assert best["provider_profile"] == "c" and best["metrics"]["poi_count"] == 2
    report = update["optimization"]
    assert report["pois"] == ["Burg", "Kloster"]
    assert report["poi_status"] == "ok" and report["poi_corridor_m"] == 300.0
    counts = {c["provider_profile"]: c["metrics"]["poi_count"] for c in update["candidates"]}
    assert counts == {"a": 1, "b": 2, "c": 2}


@pytest.mark.parametrize(
    "service",
    [None, StubPois(error=ProviderUnavailableError("busy", provider="overpass"))],
)
async def test_without_a_working_poi_lookup_the_goal_is_dropped_and_the_plan_goes_on(service):
    update = await build_optimize_node(poi_service=service)(state())  # type: ignore[arg-type]
    report = update["optimization"]
    assert report["poi_status"] == "unavailable"
    assert report["dropped_goals"][0]["metric"] == "pois"
    assert update["selected_candidate"]["provider_profile"] == "a"  # the usual order stands


async def test_no_poi_lookup_happens_unless_the_goal_was_asked_for():
    stub = StubPois()
    spec = {"goals": [{"metric": "distance", "weight": 1.0}]}
    update = await build_optimize_node(poi_service=stub)(state(optimize=spec))  # type: ignore[arg-type]
    assert stub.calls == [] and "poi_status" not in update["optimization"]
