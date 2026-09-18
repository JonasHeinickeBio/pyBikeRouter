"""Tests for the BRouter adapter against recorded-style fixtures.

BRouter responds with plain-text errors (no JSON bodies) and string-typed
GeoJSON summary values; both shapes are pinned here together with the
request-parameter contract (lonlats ordering, custom_ profile names).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from bike_routing_agent.errors import (
    ProviderBadResponseError,
    ProviderNoRouteError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from bike_routing_agent.models import Coordinate, RouteConstraints, RoutingRequest
from bike_routing_agent.providers.brouter import BRouterAdapter

BROUTER_BASE = "http://brouter.test"
BROUTER_URL = f"{BROUTER_BASE}/brouter"
ROBOTS_URL = f"{BROUTER_BASE}/robots.txt"


def make_request(**constraint_kwargs: object) -> RoutingRequest:
    return RoutingRequest(
        origin=Coordinate(lon=10.5267132, lat=52.2689081),
        destination=Coordinate(lon=10.5450128, lat=52.2201356),
        constraints=RouteConstraints(bike_type="gravel", **constraint_kwargs),  # type: ignore[arg-type]
    )


@respx.mock
async def test_route_normalizes_fixture_into_candidate(brouter_route_response: dict) -> None:
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, json=brouter_route_response))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    candidate = await adapter.route(make_request())

    assert candidate.provider == "brouter"
    assert candidate.provider_profile == "custom_gravel-v1"
    assert candidate.metrics.distance_m == 5761.0
    assert candidate.metrics.duration_s == 1224.0
    assert candidate.metrics.ascent_m == 64.0
    assert candidate.metrics.descent_m is None
    assert candidate.geometry_geojson["type"] == "LineString"
    assert candidate.provenance == {"provider": "brouter", "profile": "custom_gravel-v1"}
    assert candidate.raw_provider_response == brouter_route_response
    assert candidate.warnings == []


@respx.mock
async def test_route_sends_lonlats_and_profile_params(brouter_route_response: dict) -> None:
    route = respx.get(BROUTER_URL).mock(
        return_value=httpx.Response(200, json=brouter_route_response)
    )
    adapter = BRouterAdapter(base_url=f"{BROUTER_BASE}/")

    await adapter.route(make_request())

    params = route.calls[0].request.url.params
    assert params["lonlats"] == "10.5267132,52.2689081|10.5450128,52.2201356"
    assert params["profile"] == "custom_gravel-v1"
    assert params["alternativeidx"] == "0"
    assert params["format"] == "geojson"


@respx.mock
async def test_route_keeps_via_order_in_lonlats(brouter_route_response: dict) -> None:
    route = respx.get(BROUTER_URL).mock(
        return_value=httpx.Response(200, json=brouter_route_response)
    )
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    request = RoutingRequest(
        origin=Coordinate(lon=1.0, lat=2.0),
        via=[Coordinate(lon=3.0, lat=4.0)],
        destination=Coordinate(lon=5.0, lat=6.0),
        constraints=RouteConstraints(bike_type="city"),
    )
    await adapter.route(request)

    assert (
        route.calls[0].request.url.params["lonlats"]
        == "1.0000000,2.0000000|3.0000000,4.0000000|5.0000000,6.0000000"
    )
    assert route.calls[0].request.url.params["profile"] == "trekking"


@respx.mock
async def test_accepts_bare_feature_without_feature_collection() -> None:
    payload = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[1.0, 2.0], [3.0, 4.0]]},
        "properties": {"track-length": "100", "total-time": "30"},
    }
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, json=payload))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    candidate = await adapter.route(make_request())

    assert candidate.metrics.distance_m == 100.0
    assert candidate.metrics.duration_s == 30.0
    assert candidate.metrics.ascent_m is None


@respx.mock
async def test_unreachable_target_maps_400_text_to_no_route_error() -> None:
    respx.get(BROUTER_URL).mock(
        return_value=httpx.Response(
            400, text="20020001: routing failed - start not reachable from network"
        )
    )
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    with pytest.raises(ProviderNoRouteError) as exc_info:
        await adapter.route(make_request())

    assert exc_info.value.provider == "brouter"
    assert "not reachable" in exc_info.value.detail["body"]


@respx.mock
async def test_unknown_profile_maps_400_text_to_bad_response_error() -> None:
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(400, text="cannot find profile 'nope'"))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    with pytest.raises(ProviderBadResponseError) as exc_info:
        await adapter.route(make_request())

    assert exc_info.value.detail["status_code"] == 400
    assert "cannot find profile" in exc_info.value.detail["body"]


@respx.mock
async def test_server_error_maps_to_unavailable_after_retries() -> None:
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(500, text="java.lang.RuntimeException"))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    with pytest.raises(ProviderUnavailableError) as exc_info:
        await adapter.route(make_request())

    assert exc_info.value.detail["status_code"] == 500
    assert "RuntimeException" in exc_info.value.detail["body"]


@respx.mock
async def test_retries_500_then_succeeds(brouter_route_response: dict) -> None:
    respx.get(BROUTER_URL).mock(
        side_effect=[
            httpx.Response(500, text="too busy"),
            httpx.Response(200, json=brouter_route_response),
        ]
    )
    adapter = BRouterAdapter(base_url=BROUTER_BASE, max_retries=1)

    candidate = await adapter.route(make_request())

    assert candidate.metrics.distance_m == 5761.0
    assert len(respx.calls) == 2


@respx.mock
async def test_timeout_maps_to_provider_timeout() -> None:
    respx.get(BROUTER_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    adapter = BRouterAdapter(base_url=BROUTER_BASE, timeout_s=0.1)

    with pytest.raises(ProviderTimeoutError) as exc_info:
        await adapter.route(make_request())

    assert exc_info.value.detail["attempts"] == 1


@respx.mock
async def test_connection_error_maps_to_unavailable() -> None:
    respx.get(BROUTER_URL).mock(side_effect=httpx.ConnectError("refused"))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    with pytest.raises(ProviderUnavailableError) as exc_info:
        await adapter.route(make_request())

    assert "refused" in exc_info.value.detail["error"]


@respx.mock
async def test_invalid_json_maps_to_bad_response() -> None:
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, text="<html>not geojson</html>"))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    with pytest.raises(ProviderBadResponseError) as exc_info:
        await adapter.route(make_request())

    assert "invalid JSON" in str(exc_info.value)


@respx.mock
async def test_non_object_payload_maps_to_bad_response() -> None:
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, json=["unexpected"]))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    with pytest.raises(ProviderBadResponseError):
        await adapter.route(make_request())


@respx.mock
async def test_no_linestring_feature_maps_to_no_route() -> None:
    respx.get(BROUTER_URL).mock(
        return_value=httpx.Response(
            200,
            json={"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}}]},
        )
    )
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    with pytest.raises(ProviderNoRouteError):
        await adapter.route(make_request())


@respx.mock
async def test_missing_track_length_maps_to_bad_response() -> None:
    respx.get(BROUTER_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[1.0, 2.0]]},
                "properties": {"total-time": "30"},
            },
        )
    )
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    with pytest.raises(ProviderBadResponseError) as exc_info:
        await adapter.route(make_request())

    assert "track-length" in str(exc_info.value)


@respx.mock
async def test_surface_preferences_produce_warning(brouter_route_response: dict) -> None:
    respx.get(BROUTER_URL).mock(return_value=httpx.Response(200, json=brouter_route_response))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    candidate = await adapter.route(make_request(prefer_surfaces=["gravel", "unpaved"]))

    assert len(candidate.warnings) == 1
    assert "gravel" in candidate.warnings[0]
    assert "cannot be applied per request" in candidate.warnings[0]


@respx.mock
async def test_health_ok_on_robots_200() -> None:
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow: /"))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    assert await adapter.health() == {"status": "ok"}


@respx.mock
async def test_health_degraded_on_non_200() -> None:
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(503))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    assert await adapter.health() == {"status": "degraded", "status_code": 503}


@respx.mock
async def test_health_unavailable_on_connection_error() -> None:
    respx.get(ROBOTS_URL).mock(side_effect=httpx.ConnectError("refused"))
    adapter = BRouterAdapter(base_url=BROUTER_BASE)

    result = await adapter.health()

    assert result["status"] == "unavailable"
    assert "refused" in result["error"]
