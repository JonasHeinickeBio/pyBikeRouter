"""Tests for the Valhalla adapter against recorded-style fixtures.

Valhalla-specific shapes pinned here: base64 polyline6 geometry (shared
``trip.shapes`` by leg index and inline leg shapes), kilometer summaries,
elevation arrays -> ascent/descent, and the JSON error bodies with
``message`` text mapping to no-route vs bad-response errors.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from bike_routing_agent.errors import (
    ProviderBadResponseError,
    ProviderNoRouteError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from bike_routing_agent.models import Coordinate, RouteConstraints, RoutingRequest
from bike_routing_agent.providers.valhalla import ValhallaAdapter, decode_polyline6

VALHALLA_BASE = "http://valhalla.test"
ROUTE_URL = f"{VALHALLA_BASE}/route"
STATUS_URL = f"{VALHALLA_BASE}/status"

# Decoded geometry of the fixture polylines (fixture encoding is pinned by
# tests/providers/test_valhalla_adapter.py::test_polyline6_roundtrip).
SHAPE_A = [[13.387208, 52.527742], [13.395315, 52.524799], [13.402966, 52.521284]]
SHAPE_A_FULL = [*SHAPE_A, [13.409315, 52.515912]]


def make_request(**constraint_kwargs: object) -> RoutingRequest:
    constraint_kwargs.setdefault("bike_type", "city")
    return RoutingRequest(
        origin=Coordinate(lon=13.387208, lat=52.527742),
        destination=Coordinate(lon=13.419405, lat=52.506832),
        constraints=RouteConstraints(**constraint_kwargs),  # type: ignore[arg-type]
    )


def test_polyline6_decodes_fixture_shape(valhalla_route_response: dict) -> None:
    assert decode_polyline6(valhalla_route_response["trip"]["shapes"][0]) == SHAPE_A_FULL


@respx.mock
async def test_route_normalizes_fixture_into_candidate(valhalla_route_response: dict) -> None:
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    candidate = await adapter.route(make_request())

    assert candidate.provider == "valhalla"
    assert candidate.provider_profile == "bicycle"
    assert candidate.metrics.distance_m == pytest.approx(2412.0)
    assert candidate.metrics.duration_s == pytest.approx(540.0)
    assert candidate.metrics.ascent_m == pytest.approx(1.1)
    assert candidate.metrics.descent_m == pytest.approx(1.3)
    assert candidate.geometry_geojson["type"] == "LineString"
    assert candidate.geometry_geojson["coordinates"] == SHAPE_A_FULL
    assert candidate.provenance == {"provider": "valhalla", "profile": "bicycle"}
    assert candidate.raw_provider_response == valhalla_route_response
    # Default constraints avoid ferries and high-traffic roads, so the only
    # warning left is the unsupported surface handling... which is also
    # absent by default: a clean run carries no warnings.
    assert candidate.warnings == []


@respx.mock
async def test_route_sends_bicycle_costing_and_units(valhalla_route_response: dict) -> None:
    call = respx.post(ROUTE_URL).mock(
        return_value=httpx.Response(200, json=valhalla_route_response)
    )
    adapter = ValhallaAdapter(base_url=f"{VALHALLA_BASE}/")

    await adapter.route(make_request())

    body = json.loads(call.calls[0].request.content)
    assert body["costing"] == "bicycle"
    assert body["units"] == "kilometers"
    assert body["elevation_interval"] == 30
    assert body["locations"] == [
        {"lon": 13.387208, "lat": 52.527742},
        {"lon": 13.419405, "lat": 52.506832},
    ]
    # Default constraints translate into hard avoidances.
    assert body["costing_options"]["bicycle"] == {"use_ferry": 0.0, "use_highways": 0.0}


@respx.mock
async def test_route_keeps_via_order(valhalla_route_response: dict) -> None:
    call = respx.post(ROUTE_URL).mock(
        return_value=httpx.Response(200, json=valhalla_route_response)
    )
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    request = RoutingRequest(
        origin=Coordinate(lon=1.0, lat=2.0),
        via=[Coordinate(lon=3.0, lat=4.0)],
        destination=Coordinate(lon=5.0, lat=6.0),
        constraints=RouteConstraints(bike_type="touring"),
    )
    await adapter.route(request)

    body = __import__("json").loads(call.calls[0].request.content)
    assert body["locations"] == [
        {"lon": 1.0, "lat": 2.0},
        {"lon": 3.0, "lat": 4.0},
        {"lon": 5.0, "lat": 6.0},
    ]


@respx.mock
async def test_relaxed_constraints_omit_avoidances(valhalla_route_response: dict) -> None:
    call = respx.post(ROUTE_URL).mock(
        return_value=httpx.Response(200, json=valhalla_route_response)
    )
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    candidate = await adapter.route(
        make_request(avoid_ferries=False, avoid_high_traffic_roads=False)
    )

    body = __import__("json").loads(call.calls[0].request.content)
    assert body["costing_options"]["bicycle"] == {}
    assert any("avoid_ferries=False" in w for w in candidate.warnings)


@respx.mock
async def test_multileg_shapes_are_concatenated_without_duplicate_junction(
    valhalla_route_response_multileg: dict,
) -> None:
    respx.post(ROUTE_URL).mock(
        return_value=httpx.Response(200, json=valhalla_route_response_multileg)
    )
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    candidate = await adapter.route(make_request())

    coordinates = candidate.geometry_geojson["coordinates"]
    assert coordinates == [*SHAPE_A_FULL, [13.413194, 52.511109], [13.419405, 52.506832]]
    assert candidate.metrics.distance_m == pytest.approx(3338.0)
    assert candidate.metrics.ascent_m == pytest.approx(1.4)
    assert candidate.metrics.descent_m == pytest.approx(2.6)


@respx.mock
async def test_inline_leg_shape_is_decoded(
    valhalla_route_response_inline_shape: dict,
) -> None:
    respx.post(ROUTE_URL).mock(
        return_value=httpx.Response(200, json=valhalla_route_response_inline_shape)
    )
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    candidate = await adapter.route(make_request())

    assert candidate.geometry_geojson["coordinates"] == SHAPE_A_FULL


@respx.mock
async def test_missing_elevation_leaves_ascent_none(valhalla_route_response: dict) -> None:
    del valhalla_route_response["trip"]["legs"][0]["elevation"]
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    candidate = await adapter.route(make_request())

    assert candidate.metrics.ascent_m is None
    assert candidate.metrics.descent_m is None


@respx.mock
async def test_provider_warnings_are_forwarded(valhalla_route_response: dict) -> None:
    valhalla_route_response["trip"]["warnings"] = [
        {"text": "Using fall-back cycleway network", "code": 105}
    ]
    valhalla_route_response["trip"]["summary"]["has_highway"] = True
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    candidate = await adapter.route(make_request())

    assert "Using fall-back cycleway network" in candidate.warnings
    assert any("has_highway=true" in w for w in candidate.warnings)


@respx.mock
async def test_ebike_gets_no_e_assist_costing_warning(valhalla_route_response: dict) -> None:
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    candidate = await adapter.route(make_request(bike_type="ebike"))

    assert any("no e-assist costing" in w for w in candidate.warnings)


@respx.mock
async def test_surface_preferences_produce_warning(valhalla_route_response: dict) -> None:
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    candidate = await adapter.route(make_request(prefer_surfaces=["paved"]))

    assert any("surface preferences are not applied" in w for w in candidate.warnings)
    assert any("paved" in w for w in candidate.warnings)


@respx.mock
async def test_no_route_error_maps_to_no_route_error(valhalla_no_route_response: dict) -> None:
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(400, json=valhalla_no_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderNoRouteError) as exc_info:
        await adapter.route(make_request())

    assert exc_info.value.provider == "valhalla"
    assert exc_info.value.detail["status_code"] == 400


@respx.mock
async def test_unconnected_regions_map_to_no_route_error() -> None:
    respx.post(ROUTE_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "statusCode": 400,
                "status": "Bad Request",
                "message": "Locations are in unconnected regions. Go check/edit the map",
            },
        )
    )
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderNoRouteError):
        await adapter.route(make_request())


@respx.mock
async def test_bad_request_maps_to_bad_response_error() -> None:
    respx.post(ROUTE_URL).mock(
        return_value=httpx.Response(
            400, json={"statusCode": 400, "status": "Bad Request", "message": "Nope"}
        )
    )
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderBadResponseError) as exc_info:
        await adapter.route(make_request())

    assert exc_info.value.detail["status_code"] == 400


@respx.mock
async def test_nonzero_trip_status_maps_to_no_route_error() -> None:
    respx.post(ROUTE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"trip": {"status": 442, "status_message": "No path could be found for input"}},
        )
    )
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderNoRouteError):
        await adapter.route(make_request())


@respx.mock
async def test_rate_limit_maps_to_rate_limit_error() -> None:
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(429, text="slow down"))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderRateLimitError):
        await adapter.route(make_request())


@respx.mock
async def test_server_error_maps_to_unavailable_after_retries() -> None:
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(503, text="loading tiles"))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE, max_retries=0)

    with pytest.raises(ProviderUnavailableError) as exc_info:
        await adapter.route(make_request())

    assert exc_info.value.detail["status_code"] == 503


@respx.mock
async def test_retries_500_then_succeeds(valhalla_route_response: dict) -> None:
    respx.post(ROUTE_URL).mock(
        side_effect=[
            httpx.Response(500, text="busy"),
            httpx.Response(200, json=valhalla_route_response),
        ]
    )
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE, max_retries=1)

    candidate = await adapter.route(make_request())

    assert candidate.metrics.distance_m == pytest.approx(2412.0)
    assert len(respx.calls) == 2


@respx.mock
async def test_timeout_maps_to_provider_timeout() -> None:
    respx.post(ROUTE_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE, timeout_s=0.1)

    with pytest.raises(ProviderTimeoutError) as exc_info:
        await adapter.route(make_request())

    assert exc_info.value.detail["attempts"] == 1


@respx.mock
async def test_connection_error_maps_to_unavailable() -> None:
    respx.post(ROUTE_URL).mock(side_effect=httpx.ConnectError("refused"))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderUnavailableError) as exc_info:
        await adapter.route(make_request())

    assert "refused" in exc_info.value.detail["error"]


@respx.mock
async def test_invalid_json_maps_to_bad_response() -> None:
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, text="<html>not json</html>"))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderBadResponseError) as exc_info:
        await adapter.route(make_request())

    assert "invalid JSON" in str(exc_info.value)


@respx.mock
async def test_missing_trip_maps_to_bad_response() -> None:
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json={"no_trip": True}))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderBadResponseError):
        await adapter.route(make_request())


@respx.mock
async def test_missing_shape_index_maps_to_bad_response(valhalla_route_response: dict) -> None:
    valhalla_route_response["trip"]["legs"][0]["shape"] = 7
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderBadResponseError) as exc_info:
        await adapter.route(make_request())

    assert "out of range" in str(exc_info.value)


@respx.mock
async def test_undecodable_shape_maps_to_bad_response(valhalla_route_response: dict) -> None:
    valhalla_route_response["trip"]["shapes"] = ["not base64 at all!!"]
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderBadResponseError) as exc_info:
        await adapter.route(make_request())

    assert "undecodable" in str(exc_info.value)


async def test_health_ok_on_status_200() -> None:
    respx_mock = respx.mock
    with respx_mock:
        respx.get(STATUS_URL).mock(return_value=httpx.Response(200, json={"status": "OK"}))
        adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

        assert await adapter.health() == {"status": "ok"}


async def test_health_degraded_while_loading() -> None:
    with respx.mock:
        respx.get(STATUS_URL).mock(return_value=httpx.Response(503))
        adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

        assert await adapter.health() == {"status": "degraded", "status_code": 503}


async def test_health_unavailable_on_connection_error() -> None:
    with respx.mock:
        respx.get(STATUS_URL).mock(side_effect=httpx.ConnectError("refused"))
        adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

        result = await adapter.health()

        assert result["status"] == "unavailable"
        assert "refused" in result["error"]


def test_decode_polyline6_rejects_truncated_encoding() -> None:
    # Valid start ('\x8b\x01' -> one delta) then a lone continuation byte.
    import base64

    truncated = base64.b64encode(bytes([0x8B, 0xA1])).decode()
    with pytest.raises(ProviderBadResponseError) as exc_info:
        decode_polyline6(truncated)

    assert "truncated" in str(exc_info.value)
