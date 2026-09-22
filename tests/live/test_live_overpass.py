"""Live end-to-end test against the real public Overpass API — no mocks.

Excluded from the default test run (see tool.pytest.ini_options.addopts); run
explicitly with `pytest -m live`. Corridor: a ~1 km straight-line route
through central Braunschweig (Schloss -> east along the Kastanienwall /
Kasseler Straße axis), a dense mixed-traffic area where OSM surface
tagging is well established.

Courtecy + robustness against the shared public instance: one module-scoped
fixture issues the single query the whole module needs (the enricher's cache
serves every test from it) and backs off on 429/504s; when the instance
stays busy the module skips instead of failing — overload of a third-party
service is not this repo's bug (error mapping is unit-tested in
tests/enrichment/test_overpass.py).

Assertions are deliberately tolerant of OSM edit churn: they pin the
*shape* of a believable urban surface profile (mapped surfaces exist, hard
surfacing dominates, fractions are consistent), never exact tag counts.
"""

from __future__ import annotations

import asyncio

import pytest

from bike_routing_agent.config import settings
from bike_routing_agent.enrichment.geometry import polyline_length_m
from bike_routing_agent.enrichment.overpass import OverpassEnricher
from bike_routing_agent.errors import (
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from bike_routing_agent.models import Coordinate, RouteCandidate, RouteMetrics
from bike_routing_agent.nodes.enrich import PROVENANCE_KEY, build_enrich_node
from bike_routing_agent.providers.base import InMemoryTTLCache

pytestmark = pytest.mark.live

_BUSY = (ProviderRateLimitError, ProviderUnavailableError, ProviderTimeoutError)

# One enricher + cache for the module: the warm-up below is the only HTTP
# traffic this module ever generates against the public instance.
_ENRICHER = OverpassEnricher(
    base_url=settings.overpass_base_url,
    timeout_s=30.0,
    max_retries=3,
    buffer_m=40.0,
    cache=InMemoryTTLCache(),
)

# Braunschweig, Schloss (lon 10.5260, lat 52.2672) eastward ~1 km.
CORRIDOR = [Coordinate(lon=10.5260 + i * 0.0021, lat=52.2672) for i in range(8)]


@pytest.fixture(scope="module", autouse=True)
async def _warm_shared_cache_or_skip():
    for attempt in range(3):
        try:
            await _ENRICHER.surface_profile(CORRIDOR)
            return
        except _BUSY:
            if attempt < 2:
                await asyncio.sleep(15.0)
    pytest.skip("public Overpass unreachable or persistently rate limited")


async def test_real_corridor_has_a_believable_urban_surface_profile() -> None:
    summary = await _ENRICHER.surface_profile(CORRIDOR)  # served from cache

    # Length sanity: the corridor is ~1.0 km of straight line.
    assert summary.total_m == pytest.approx(polyline_length_m(CORRIDOR), rel=0.01)
    assert summary.total_m == pytest.approx(1000.0, rel=0.05)

    # Central Braunschweig is thoroughly surface-tagged and hard-surfaced:
    # the historic centre is largely cobblestone, so the believable profile
    # is dominated by masonry/compacted/paved together, with low unknown.
    assert summary.coverage, "no mapped surfaces within 40 m of a city-centre corridor"
    hard = sum(summary.coverage.get(k, 0.0) for k in ("paved", "compacted", "masonry"))
    assert hard >= 0.5, f"expected hard surfaces in the old town, got {summary.coverage}"
    assert summary.unknown_fraction < 0.5

    # Policy consistency (docs/enrichment.md): coverage + unknown = 1, and
    # conflicts stay a subset of unknown.
    assert sum(summary.coverage.values()) + summary.unknown_fraction == pytest.approx(1.0)
    assert summary.conflict_fraction <= summary.unknown_fraction + 1e-9
    assert 0.0 <= summary.inferred_fraction <= 1.0

    # Road-class mix is route-relative: fractions are shares of the total
    # corridor length, so unmatched segments simply do not contribute, and
    # the matched share is at least the surface-known share.
    assert summary.highway_fractions
    highway_share = sum(summary.highway_fractions.values())
    assert 0.0 < highway_share <= 1.0 + 1e-9
    assert highway_share >= 1.0 - summary.unknown_fraction - 1e-9


async def test_real_enrichment_flows_through_the_graph_node() -> None:
    candidate = RouteCandidate(
        provider="live",
        provider_profile="cycling-regular",
        geometry_geojson={
            "type": "LineString",
            "coordinates": [[p.lon, p.lat] for p in CORRIDOR],
        },
        metrics=RouteMetrics(distance_m=1000.0, duration_s=240.0),
    ).model_dump(mode="json")

    result = await build_enrich_node(surface_enricher=_ENRICHER)({"candidates": [candidate]})

    assert "errors" not in result
    updated = RouteCandidate.model_validate(result["candidates"][0])
    assert updated.provenance[PROVENANCE_KEY]["status"] == "ok"
    assert updated.metrics.surface_coverage
    assert updated.metrics.unknown_surface_fraction is not None
    assert 0.0 <= updated.metrics.unknown_surface_fraction <= 1.0
