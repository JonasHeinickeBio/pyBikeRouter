"""Overpass surface enricher tests against recorded-style fixtures.

Overpass-specific shapes pinned here: POST form encoding (``data=<query>``),
lat-first ``out geom`` geometries, decimation of long shapes, and the
error mapping for rate limiting / server errors / timeouts.
"""

from __future__ import annotations

import math
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from bike_routing_agent.enrichment.overpass import (
    USER_AGENT,
    OverpassEnricher,
    _parse_ways,
    build_overpass_query,
)
from bike_routing_agent.errors import (
    ProviderBadResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from bike_routing_agent.models import Coordinate
from bike_routing_agent.providers.base import InMemoryTTLCache

OVERPASS_URL = "http://overpass.test/api/interpreter"

# Straight east-bound route along lat 52.5 from lon 13.400 to 13.410, eight
# equal segments; the fixture ways cover it in three stretches of 3/8, 2/8
# and 3/8 segments (asphalt residential / grade2 track / gravel+paved path).
ROUTE_POINTS = [Coordinate(lon=13.400 + i * 0.00125, lat=52.5) for i in range(9)]


def make_enricher(**overrides: object) -> OverpassEnricher:
    kwargs: dict = {"base_url": OVERPASS_URL}
    kwargs.update(overrides)
    return OverpassEnricher(**kwargs)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Query construction
# ----------------------------------------------------------------------


def test_build_overpass_query_renders_around_clause() -> None:
    query = build_overpass_query(ROUTE_POINTS, buffer_m=25.0, timeout_s=20.0)
    assert query.startswith("[out:json][timeout:20];")
    assert query.endswith(")[highway];out tags geom;")
    assert "way(around:25.0,52.500000,13.400000" in query
    assert query.count(",") == 2 * len(ROUTE_POINTS)  # buffer sep + lat/lon separators


def test_build_overpass_query_rounds_timeout_up_to_a_second() -> None:
    assert "[timeout:21];" in build_overpass_query(
        ROUTE_POINTS, buffer_m=25.0, timeout_s=20.1
    )


def test_build_overpass_query_decimates_long_shapes() -> None:
    long_route = [Coordinate(lon=13.4 + i * 1e-5, lat=52.5) for i in range(1000)]
    query = build_overpass_query(long_route, buffer_m=25.0, timeout_s=20.0)
    inner = query.split("way(around:25.0,", 1)[1].rsplit(")[highway]", 1)[0]
    coords = inner.split(",")
    assert len(coords) == 2 * 400  # _MAX_QUERY_POINTS, first/last preserved
    assert coords[0] == f"{long_route[0].lat:.6f}"
    assert coords[-2] == f"{long_route[-1].lat:.6f}"
    assert coords[-1] == f"{long_route[-1].lon:.6f}"


def test_buffer_must_be_positive() -> None:
    with pytest.raises(ValueError, match="buffer_m"):
        OverpassEnricher(buffer_m=0)


# ----------------------------------------------------------------------
# Profile against fixtures
# ----------------------------------------------------------------------


@respx.mock
async def test_surface_profile_from_fixture(overpass_surface_response: dict) -> None:
    route = respx.post(OVERPASS_URL).mock(
        return_value=httpx.Response(200, json=overpass_surface_response)
    )
    enricher = make_enricher()

    summary = await enricher.surface_profile(ROUTE_POINTS)

    assert route.call_count == 1
    # Segment coverage: 3/8 explicit paved, 2/8 inferred compacted (tracktype),
    # 3/8 conflict (gravel + paved=yes) -> unknown. The fixture's node and
    # two malformed ways are skipped without failing the corridor.
    assert summary.coverage == {
        "paved": pytest.approx(3 / 8),
        "compacted": pytest.approx(2 / 8),
    }
    assert summary.unknown_fraction == pytest.approx(3 / 8)
    assert summary.conflict_fraction == pytest.approx(3 / 8)
    assert summary.inferred_fraction == pytest.approx(2 / 8)
    assert summary.highway_fractions == {
        "residential": pytest.approx(3 / 8),
        "track": pytest.approx(2 / 8),
        "path": pytest.approx(3 / 8),
    }
    # Access visibility rides along even on the surface-conflicted path way
    # (fixture's mixed-case "Private" normalises to lower case).
    assert summary.access_fractions == {
        "destination": pytest.approx(3 / 8),
        "private": pytest.approx(3 / 8),
    }
    # ~678 m of route (8 segments of 0.00125 deg lon at lat 52.5).
    assert summary.total_m == pytest.approx(678.2, abs=1.0)


@respx.mock
async def test_surface_profile_posts_form_encoded_query() -> None:
    call = respx.post(OVERPASS_URL).mock(
        return_value=httpx.Response(200, json={"elements": []})
    )
    await make_enricher().surface_profile(ROUTE_POINTS)

    request = call.calls[0].request
    assert request.headers["user-agent"] == USER_AGENT
    form = parse_qs(request.content.decode())
    (query,) = form["data"]
    assert query.startswith("[out:json][timeout:20];way(around:25.0,")


def test_out_of_range_geometry_point_skips_only_its_way() -> None:
    # Coordinate validates |lat|<=90 / |lon|<=180; one poisoned point must
    # drop its way, never abort parsing of the whole corridor.
    payload = {
        "elements": [
            {
                "type": "way",
                "id": 1,
                "tags": {"highway": "residential"},
                "geometry": [{"lat": 52.5, "lon": 13.4}, {"lat": 999.0, "lon": 13.5}],
            },
            {
                "type": "way",
                "id": 2,
                "tags": {"highway": "cycleway"},
                "geometry": [{"lat": 52.5, "lon": 13.6}, [52.5, 181.0]],
            },
            {
                "type": "way",
                "id": 3,
                "tags": {"highway": "path"},
                "geometry": [{"lat": 52.5, "lon": 13.4}, {"lat": 52.6, "lon": 13.5}],
            },
        ]
    }
    ways = _parse_ways(payload, provider="overpass")
    assert [w.way_id for w in ways] == [3]


@respx.mock
async def test_empty_corridor_is_fully_unknown(overpass_empty_response: dict) -> None:
    respx.post(OVERPASS_URL).mock(
        return_value=httpx.Response(200, json=overpass_empty_response)
    )
    summary = await make_enricher().surface_profile(ROUTE_POINTS)
    assert summary.coverage == {}
    assert summary.unknown_fraction == pytest.approx(1.0)
    assert summary.total_m == pytest.approx(678.2, abs=1.0)


@respx.mock
async def test_missing_shape_points_rejected_without_http() -> None:
    with respx.mock(assert_all_called=False) as mock:
        stub = mock.post(OVERPASS_URL).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(ProviderBadResponseError, match="fewer than two"):
            await make_enricher().surface_profile([Coordinate(lon=13.4, lat=52.5)])
    assert stub.call_count == 0


# ----------------------------------------------------------------------
# Error mapping
# ----------------------------------------------------------------------


@respx.mock
async def test_non_overpass_payload_is_bad_response() -> None:
    respx.post(OVERPASS_URL).mock(return_value=httpx.Response(200, json={"error": "boom"}))
    with pytest.raises(ProviderBadResponseError, match="malformed"):
        await make_enricher().surface_profile(ROUTE_POINTS)


@respx.mock
async def test_invalid_json_is_bad_response() -> None:
    respx.post(OVERPASS_URL).mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(ProviderBadResponseError, match="invalid JSON"):
        await make_enricher().surface_profile(ROUTE_POINTS)


@respx.mock
async def test_429_maps_to_rate_limit_without_retry() -> None:
    route = respx.post(OVERPASS_URL).mock(return_value=httpx.Response(429, text="slow down"))
    with pytest.raises(ProviderRateLimitError) as excinfo:
        await make_enricher().surface_profile(ROUTE_POINTS)
    assert excinfo.value.to_dict()["code"] == "provider_rate_limited"
    assert route.call_count == 1


@respx.mock
async def test_server_error_is_retried_then_unavailable() -> None:
    route = respx.post(OVERPASS_URL).mock(
        side_effect=[httpx.Response(504, text="deadline"), httpx.Response(504, text="deadline")]
    )
    with pytest.raises(ProviderUnavailableError) as excinfo:
        await make_enricher(max_retries=1).surface_profile(ROUTE_POINTS)
    error = excinfo.value.to_dict()
    assert error["code"] == "provider_unavailable"
    assert error["detail"]["status_code"] == 504
    assert error["detail"]["attempts"] == 2
    assert route.call_count == 2


@respx.mock
async def test_server_error_recovers_on_retry(overpass_empty_response: dict) -> None:
    route = respx.post(OVERPASS_URL).mock(
        side_effect=[
            httpx.Response(500, text="transient"),
            httpx.Response(200, json=overpass_empty_response),
        ]
    )
    summary = await make_enricher(max_retries=1).surface_profile(ROUTE_POINTS)
    assert summary.unknown_fraction == pytest.approx(1.0)
    assert route.call_count == 2


@respx.mock
async def test_timeout_is_retried_then_reported() -> None:
    route = respx.post(OVERPASS_URL).mock(
        side_effect=[httpx.ConnectTimeout("slow"), httpx.ConnectTimeout("slow")]
    )
    with pytest.raises(ProviderTimeoutError) as excinfo:
        await make_enricher(max_retries=1).surface_profile(ROUTE_POINTS)
    assert excinfo.value.to_dict()["detail"]["attempts"] == 2
    assert route.call_count == 2


@respx.mock
async def test_connection_error_is_unavailable_without_retry() -> None:
    route = respx.post(OVERPASS_URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ProviderUnavailableError):
        await make_enricher(max_retries=1).surface_profile(ROUTE_POINTS)
    assert route.call_count == 1


# ----------------------------------------------------------------------
# Caching & health
# ----------------------------------------------------------------------


@respx.mock
async def test_identical_shapes_hit_the_cache_once(overpass_surface_response: dict) -> None:
    route = respx.post(OVERPASS_URL).mock(
        return_value=httpx.Response(200, json=overpass_surface_response)
    )
    enricher = make_enricher(cache=InMemoryTTLCache())

    first = await enricher.surface_profile(ROUTE_POINTS)
    second = await enricher.surface_profile(ROUTE_POINTS)

    assert route.call_count == 1
    assert second.coverage == first.coverage


@respx.mock
async def test_health_probe_status_mapping() -> None:
    enricher = make_enricher()
    respx.post(OVERPASS_URL).mock(return_value=httpx.Response(200, json={"elements": []}))
    assert await enricher.health() == {"status": "ok"}

    respx.post(OVERPASS_URL).mock(return_value=httpx.Response(504, text="busy"))
    assert await enricher.health() == {"status": "degraded", "status_code": 504}


@respx.mock
async def test_health_probe_transport_failure() -> None:
    respx.post(OVERPASS_URL).mock(side_effect=httpx.ConnectError("down"))
    health = await make_enricher().health()
    assert health["status"] == "unavailable"
    assert isinstance(health["error"], str)


def test_tolerance_is_two_times_buffer() -> None:
    # Pinned policy: a way within 2x buffer of a segment midpoint counts.
    enricher = make_enricher(buffer_m=10.0)
    assert enricher._buffer_m == pytest.approx(10.0)
    from bike_routing_agent.enrichment import overpass as module

    assert module._TOLERANCE_FACTOR == pytest.approx(2.0)
    assert math.isfinite(enricher._buffer_m)
