import json

import httpx
import pytest
import respx

from bike_routing_agent.errors import (
    ProviderBadResponseError,
    ProviderNoRouteError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from bike_routing_agent.models import Coordinate, RouteConstraints, RoutingRequest
from bike_routing_agent.providers.ors import OpenRouteServiceAdapter

BASE_URL = "https://api.openrouteservice.org"
URL = f"{BASE_URL}/v2/directions/cycling-regular/geojson"


def make_request() -> RoutingRequest:
    return RoutingRequest(
        origin=Coordinate(lon=10.5267, lat=52.2689),
        destination=Coordinate(lon=10.5450, lat=52.2201),
        constraints=RouteConstraints(bike_type="gravel"),
    )


@respx.mock
async def test_route_normalizes_ors_response(ors_directions_response):
    respx.post(URL).mock(return_value=httpx.Response(200, json=ors_directions_response))
    adapter = OpenRouteServiceAdapter(api_key="key", base_url=BASE_URL, timeout_s=1.0)

    candidate = await adapter.route(make_request())

    assert candidate.provider == "ors"
    assert candidate.provider_profile == "cycling-regular"
    assert candidate.metrics.distance_m == 12543.2
    assert candidate.metrics.duration_s == 2701.5
    assert candidate.metrics.ascent_m == 84.3
    assert candidate.metrics.descent_m == 91.7
    assert candidate.geometry_geojson["type"] == "LineString"
    assert candidate.warnings
    assert candidate.provenance == {"provider": "ors", "profile": "cycling-regular"}
    assert candidate.raw_provider_response == ors_directions_response


@respx.mock
async def test_route_maps_ors_no_route_error_code(ors_no_route_response):
    respx.post(URL).mock(return_value=httpx.Response(400, json=ors_no_route_response))
    adapter = OpenRouteServiceAdapter(api_key="key", base_url=BASE_URL, timeout_s=1.0)

    with pytest.raises(ProviderNoRouteError):
        await adapter.route(make_request())


@respx.mock
async def test_route_raises_rate_limit_error_on_429():
    respx.post(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "5"}))
    adapter = OpenRouteServiceAdapter(api_key="key", base_url=BASE_URL, timeout_s=1.0)

    with pytest.raises(ProviderRateLimitError):
        await adapter.route(make_request())


@respx.mock
async def test_route_raises_timeout_error():
    respx.post(URL).mock(side_effect=httpx.TimeoutException("timed out"))
    adapter = OpenRouteServiceAdapter(
        api_key="key", base_url=BASE_URL, timeout_s=1.0, max_retries=0
    )

    with pytest.raises(ProviderTimeoutError):
        await adapter.route(make_request())


@respx.mock
async def test_route_raises_bad_response_error_on_malformed_payload():
    respx.post(URL).mock(return_value=httpx.Response(200, json={"unexpected": "shape"}))
    adapter = OpenRouteServiceAdapter(api_key="key", base_url=BASE_URL, timeout_s=1.0)

    with pytest.raises(ProviderBadResponseError):
        await adapter.route(make_request())


@respx.mock
async def test_route_drops_highways_avoid_feature_and_warns(ors_directions_response):
    """ORS only accepts "highways" as an avoid_feature for driving profiles;
    sending it for a cycling profile is a hard 400, not a soft no-op (see
    _CYCLING_AVOID_FEATURES in providers/ors.py). avoid_high_traffic_roads
    defaults to True, so every default-constraint request must not send it."""
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=ors_directions_response))
    adapter = OpenRouteServiceAdapter(api_key="key", base_url=BASE_URL, timeout_s=1.0)

    candidate = await adapter.route(make_request())

    sent_body = json.loads(route.calls[0].request.content)
    assert "highways" not in sent_body.get("options", {}).get("avoid_features", [])
    assert "ferries" in sent_body["options"]["avoid_features"]
    assert any("avoid_features" in w and "highways" in w for w in candidate.warnings)


@respx.mock
async def test_health_reports_ok_on_200():
    respx.get(f"{BASE_URL}/v2/health").mock(return_value=httpx.Response(200))
    adapter = OpenRouteServiceAdapter(api_key="key", base_url=BASE_URL, timeout_s=1.0)

    result = await adapter.health()

    assert result["status"] == "ok"
