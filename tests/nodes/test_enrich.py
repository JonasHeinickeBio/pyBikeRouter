"""Tests for the enrich_candidates graph node (issue #3).

The node decorates route candidates with surface data from a SurfaceEnricher
and must never lose or penalise candidates when enrichment fails.
"""

from __future__ import annotations

import pytest

from bike_routing_agent.enrichment.base import SurfaceSummary
from bike_routing_agent.errors import ProviderTimeoutError
from bike_routing_agent.models import Coordinate, RouteCandidate, RouteMetrics
from bike_routing_agent.nodes.enrich import build_enrich_node

GEOMETRY = {
    "type": "LineString",
    "coordinates": [[13.400, 52.5], [13.405, 52.5], [13.410, 52.5]],
}

SUMMARY = SurfaceSummary(
    total_m=678.0,
    coverage={"paved": 0.5, "compacted": 0.25},
    unknown_fraction=0.25,
    inferred_fraction=0.25,
    conflict_fraction=0.0,
    highway_fractions={"residential": 0.75},
    access_fractions={"private": 0.25},
)


class FakeEnricher:
    name = "fake"

    def __init__(self, *, summary: SurfaceSummary = SUMMARY, error: Exception | None = None):
        self._summary = summary
        self._error = error
        self.calls: list[list[Coordinate]] = []

    async def surface_profile(self, coordinates):
        self.calls.append(list(coordinates))
        if self._error is not None:
            raise self._error
        return self._summary


def candidate_dict(provider: str = "fake") -> dict:
    return RouteCandidate(
        provider=provider,
        provider_profile="cycling-regular",
        geometry_geojson=GEOMETRY,
        metrics=RouteMetrics(distance_m=678.0, duration_s=200.0, ascent_m=1.0, descent_m=1.0),
        provenance={"provider": provider},
    ).model_dump(mode="json")


async def test_no_enricher_is_a_no_op() -> None:
    node = build_enrich_node(surface_enricher=None)
    assert await node({"candidates": [candidate_dict()]}) == {}


async def test_no_candidates_is_a_no_op() -> None:
    enricher = FakeEnricher()
    node = build_enrich_node(surface_enricher=enricher)
    assert await node({"candidates": []}) == {}
    assert enricher.calls == []


async def test_success_writes_coverage_and_provenance() -> None:
    enricher = FakeEnricher()
    node = build_enrich_node(surface_enricher=enricher)

    result = await node({"candidates": [candidate_dict()]})

    (call,) = enricher.calls
    assert call == [
        Coordinate(lon=13.400, lat=52.5),
        Coordinate(lon=13.405, lat=52.5),
        Coordinate(lon=13.410, lat=52.5),
    ]
    assert "errors" not in result
    updated = RouteCandidate.model_validate(result["candidates"][0])
    assert updated.metrics.surface_coverage == SUMMARY.coverage
    assert updated.metrics.unknown_surface_fraction == pytest.approx(0.25)
    assert updated.provenance["surface_enrichment"] == {
        "status": "ok",
        "source": "fake",
        "total_m": 678.0,
        "unknown_fraction": 0.25,
        "inferred_fraction": 0.25,
        "conflict_fraction": 0.0,
        "highway_fractions": {"residential": 0.75},
        "access_fractions": {"private": 0.25},
    }
    # Nothing else about the candidate changed.
    assert updated.provider == "fake"
    assert updated.geometry_geojson == GEOMETRY
    assert updated.warnings == []


async def test_identical_geometries_share_one_lookup() -> None:
    enricher = FakeEnricher()
    node = build_enrich_node(surface_enricher=enricher)

    result = await node({"candidates": [candidate_dict("first"), candidate_dict("second")]})

    assert len(enricher.calls) == 1
    assert {c["provider"] for c in result["candidates"]} == {"first", "second"}
    assert all("surface_enrichment" in c["provenance"] for c in result["candidates"])


async def test_provider_failure_keeps_candidate_alive_with_failed_provenance() -> None:
    error = ProviderTimeoutError("timed out", provider="overpass")
    enricher = FakeEnricher(error=error)
    node = build_enrich_node(surface_enricher=enricher)

    result = await node({"candidates": [candidate_dict()]})

    updated = RouteCandidate.model_validate(result["candidates"][0])
    assert updated.metrics.surface_coverage == {}
    assert updated.metrics.unknown_surface_fraction is None  # not a fabricated 1.0
    assert updated.provenance["surface_enrichment"] == {"status": "failed", "source": "fake"}
    assert updated.warnings == []  # warnings would be penalised by the scorer
    assert result["errors"] == [error.to_dict()]


async def test_unexpected_exception_is_wrapped_as_provider_error() -> None:
    enricher = FakeEnricher(error=RuntimeError("kaboom"))
    node = build_enrich_node(surface_enricher=enricher)

    result = await node({"candidates": [candidate_dict()]})

    (error,) = result["errors"]
    assert error["code"] == "provider_error"
    assert error["provider"] == "fake"
    assert "RuntimeError" in error["message"]
    assert RouteCandidate.model_validate(result["candidates"][0])


async def test_existing_state_errors_are_preserved() -> None:
    enricher = FakeEnricher(error=ProviderTimeoutError("timed out", provider="overpass"))
    node = build_enrich_node(surface_enricher=enricher)

    result = await node(
        {"candidates": [candidate_dict()], "errors": [{"code": "earlier", "provider": "x"}]}
    )

    assert result["errors"][0]["code"] == "earlier"
    assert result["errors"][-1]["code"] == "provider_timeout"


async def test_unreadable_geometry_fails_without_calling_enricher() -> None:
    enricher = FakeEnricher()
    node = build_enrich_node(surface_enricher=enricher)
    broken = candidate_dict()
    broken["geometry_geojson"] = {"type": "LineString", "coordinates": [[13.4, 52.5]]}

    result = await node({"candidates": [broken]})

    assert enricher.calls == []
    assert result["candidates"][0]["provenance"]["surface_enrichment"]["status"] == "failed"
    assert result["errors"][0]["provider"] == "enrichment"


async def test_repeated_identical_failures_record_one_error() -> None:
    error = ProviderTimeoutError("timed out", provider="overpass")
    enricher = FakeEnricher(error=error)
    node = build_enrich_node(surface_enricher=enricher)
    first = candidate_dict("first")
    second = candidate_dict("second")
    second["geometry_geojson"] = {
        "type": "LineString",
        "coordinates": [[9.0, 50.0], [9.1, 50.0]],
    }

    result = await node({"candidates": [first, second]})

    assert len(enricher.calls) == 2  # different geometries, both attempted
    assert len(result["errors"]) == 1  # identical failure recorded once
