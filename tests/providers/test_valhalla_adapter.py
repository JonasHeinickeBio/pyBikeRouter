"""Tests for the Valhalla adapter against recorded-style fixtures.

Valhalla-specific shapes pinned here: direct polyline6 geometry carried
per leg in ``trip.legs[].shape``, kilometer summaries, elevation arrays ->
ascent/descent, and the JSON error bodies with ``message`` text mapping to
no-route vs bad-response errors.
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
from bike_routing_agent.providers import valhalla as valhalla_module
from bike_routing_agent.providers.valhalla import ValhallaAdapter, decode_polyline6

VALHALLA_BASE = "http://valhalla.test"
ROUTE_URL = f"{VALHALLA_BASE}/route"
STATUS_URL = f"{VALHALLA_BASE}/status"


def encode_polyline6(points: list[tuple[float, float]]) -> str:
    """Independent polyline6 encoder (lat, lon) -> string, for round-trips."""
    out: list[str] = []
    prev_lat = prev_lon = 0
    for lat, lon in points:
        lat_i, lon_i = round(lat * 1e6), round(lon * 1e6)
        for delta in (lat_i - prev_lat, lon_i - prev_lon):
            zigzag = (delta << 1) ^ (delta >> 63)
            while True:
                chunk = zigzag & 0x1F
                zigzag >>= 5
                if zigzag:
                    chunk |= 0x20
                out.append(chr(chunk + 63))
                if not zigzag:
                    break
        prev_lat, prev_lon = lat_i, lon_i
    return "".join(out)

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
    assert decode_polyline6(valhalla_route_response["trip"]["legs"][0]["shape"]) == SHAPE_A_FULL


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
async def test_non_string_leg_shape_maps_to_bad_response(valhalla_route_response: dict) -> None:
    valhalla_route_response["trip"]["legs"][0]["shape"] = 7
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderBadResponseError) as exc_info:
        await adapter.route(make_request())

    assert "missing an encoded shape" in str(exc_info.value)


@respx.mock
async def test_undecodable_shape_maps_to_bad_response(valhalla_route_response: dict) -> None:
    # Non-ASCII characters cannot be part of a plain-ASCII polyline6 string.
    valhalla_route_response["trip"]["legs"][0]["shape"] = "p\u00f6lyline"
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
    # A continuation character whose delta is never completed.
    with pytest.raises(ProviderBadResponseError) as exc_info:
        decode_polyline6("{")

    assert "truncated" in str(exc_info.value)


# --- additional edge cases -------------------------------------------------


def test_polyline6_roundtrip_matches_independent_encoder() -> None:
    # Covers negative deltas, multi-byte varints and antimeridian-adjacent
    # longitudes against a hand-rolled encoder written from the spec.
    points = [
        (52.527742, 13.387208),
        (52.524799, 13.395315),
        (37.441883, -122.143001),
        (-33.868820, 151.209296),
        (0.0, 0.0),
        (-0.000001, -0.000001),
    ]
    decoded = decode_polyline6(encode_polyline6(points))
    for got, (lat, lon) in zip(decoded, points, strict=True):
        assert got == pytest.approx([lon, lat])


def test_decode_polyline6_empty_string_yields_no_points() -> None:
    assert decode_polyline6("") == []


@respx.mock
async def test_degenerate_geometry_maps_to_no_route_error(
    valhalla_route_response: dict,
) -> None:
    valhalla_route_response["trip"]["legs"][0]["shape"] = encode_polyline6([(52.5, 13.4)])
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderNoRouteError) as exc_info:
        await adapter.route(make_request())

    assert "degenerate" in str(exc_info.value)


@respx.mock
async def test_missing_legs_list_maps_to_no_route_error(valhalla_route_response: dict) -> None:
    del valhalla_route_response["trip"]["legs"]
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderNoRouteError) as exc_info:
        await adapter.route(make_request())

    assert "no route legs" in str(exc_info.value)


@respx.mock
async def test_non_dict_leg_maps_to_bad_response(valhalla_route_response: dict) -> None:
    valhalla_route_response["trip"]["legs"] = ["not-a-dict"]
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderBadResponseError) as exc_info:
        await adapter.route(make_request())

    assert "malformed Valhalla trip leg" in str(exc_info.value)


@respx.mock
async def test_non_matching_leg_junctions_are_kept_verbatim(
    valhalla_route_response: dict,
) -> None:
    # Two legs whose endpoints differ: nothing is deduplicated.
    valhalla_route_response["trip"]["legs"] = [
        {"shape": encode_polyline6([(52.5, 13.4), (52.6, 13.5)])},
        {"shape": encode_polyline6([(52.7, 13.6), (52.8, 13.7)])},
    ]
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    candidate = await adapter.route(make_request())

    assert candidate.geometry_geojson["coordinates"] == [
        [13.4, 52.5],
        [13.5, 52.6],
        [13.6, 52.7],
        [13.7, 52.8],
    ]


@respx.mock
async def test_missing_summary_maps_to_bad_response(valhalla_route_response: dict) -> None:
    del valhalla_route_response["trip"]["summary"]
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderBadResponseError) as exc_info:
        await adapter.route(make_request())

    assert "missing 'summary'" in str(exc_info.value)


@respx.mock
async def test_boolean_length_is_treated_as_missing(valhalla_route_response: dict) -> None:
    # _as_float rejects bools, so a JSON true in a numeric field cannot leak
    # into the metrics as distance 1 m.
    valhalla_route_response["trip"]["summary"]["length"] = True
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderBadResponseError) as exc_info:
        await adapter.route(make_request())

    assert "missing 'length'" in str(exc_info.value)


@respx.mock
async def test_boolean_time_yields_none_duration(valhalla_route_response: dict) -> None:
    valhalla_route_response["trip"]["summary"]["time"] = False
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    candidate = await adapter.route(make_request())

    assert candidate.metrics.duration_s is None


@respx.mock
async def test_unknown_bike_type_maps_to_bad_response(
    valhalla_route_response: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(valhalla_module.VALHALLA_PROFILE_MAP, "city", raising=False)
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderBadResponseError) as exc_info:
        await adapter.route(make_request())

    assert "no Valhalla costing mapped" in str(exc_info.value)
    assert len(respx.calls) == 0  # fails before any HTTP request


@respx.mock
async def test_nonzero_trip_status_without_message_maps_to_bad_response() -> None:
    respx.post(ROUTE_URL).mock(
        return_value=httpx.Response(200, json={"trip": {"status": 442}})
    )
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    with pytest.raises(ProviderBadResponseError) as exc_info:
        await adapter.route(make_request())

    assert "HTTP 442" in str(exc_info.value)


@respx.mock
async def test_has_ferry_summary_flag_becomes_warning(valhalla_route_response: dict) -> None:
    valhalla_route_response["trip"]["summary"]["has_ferry"] = True
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    candidate = await adapter.route(make_request(avoid_ferries=False))

    assert any("has_ferry=true" in w for w in candidate.warnings)


@respx.mock
async def test_malformed_provider_warning_entries_are_dropped(
    valhalla_route_response: dict,
) -> None:
    valhalla_route_response["trip"]["warnings"] = [
        "a bare string",
        {"code": 105},
        {"text": 12},
        {"text": "kept", "code": 1},
    ]
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    candidate = await adapter.route(make_request())

    assert [w for w in candidate.warnings if "kept" in w] == ["kept"]
    assert not any("bare string" in w for w in candidate.warnings)


@respx.mock
async def test_partial_elevation_array_stops_at_null_sample(
    valhalla_route_response: dict,
) -> None:
    valhalla_route_response["trip"]["legs"][0]["elevation"] = [10.0, 12.0, None, 5.0]
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    candidate = await adapter.route(make_request())

    # 10 -> 12 counts, the null truncates the array, 5 m below is never seen.
    assert candidate.metrics.ascent_m == pytest.approx(2.0)
    assert candidate.metrics.descent_m == pytest.approx(0.0)


@respx.mock
async def test_single_sample_elevation_array_is_ignored(
    valhalla_route_response: dict,
) -> None:
    valhalla_route_response["trip"]["legs"][0]["elevation"] = [10.0]
    respx.post(ROUTE_URL).mock(return_value=httpx.Response(200, json=valhalla_route_response))
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

    candidate = await adapter.route(make_request())

    assert candidate.metrics.ascent_m is None
    assert candidate.metrics.descent_m is None


@respx.mock
async def test_retries_exhausted_on_persistent_server_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(valhalla_module.asyncio, "sleep", no_sleep)
    respx.post(ROUTE_URL).mock(
        side_effect=[httpx.Response(500, text="busy"), httpx.Response(500, text="busy")]
    )
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE, max_retries=1)

    with pytest.raises(ProviderUnavailableError) as exc_info:
        await adapter.route(make_request())

    assert exc_info.value.detail["status_code"] == 500
    assert len(respx.calls) == 2


@respx.mock
async def test_timeout_retry_counts_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(valhalla_module.asyncio, "sleep", no_sleep)
    respx.post(ROUTE_URL).mock(side_effect=[httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow")])
    adapter = ValhallaAdapter(base_url=VALHALLA_BASE, timeout_s=0.1, max_retries=1)

    with pytest.raises(ProviderTimeoutError) as exc_info:
        await adapter.route(make_request())

    assert exc_info.value.detail["attempts"] == 2


async def test_health_unavailable_on_timeout() -> None:
    with respx.mock:
        respx.get(STATUS_URL).mock(side_effect=httpx.ConnectTimeout("dial timeout"))
        adapter = ValhallaAdapter(base_url=VALHALLA_BASE)

        result = await adapter.health()

        assert result["status"] == "unavailable"
        assert "dial timeout" in result["error"]
