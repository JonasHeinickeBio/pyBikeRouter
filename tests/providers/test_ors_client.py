"""Unit tests for the full openrouteservice client (mocked HTTP via respx)."""

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
from bike_routing_agent.providers.ors_client import OpenRouteServiceClient

BASE_URL = "https://api.openrouteservice.org"

# A minimal valid GeoJSON directions payload.
DIRECTIONS_PAYLOAD = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[10.5, 52.2], [10.55, 52.21]]},
            "properties": {"summary": {"distance": 1000.0, "duration": 250.0}},
        }
    ],
}
NO_ROUTE_BODY = {"error": {"code": 2010, "message": "no path found"}}
BAD_REQUEST_BODY = {"error": {"code": 2001, "message": "Invalid request"}}


def make_client(**kwargs) -> OpenRouteServiceClient:
    defaults = {"api_key": "key", "base_url": BASE_URL, "timeout_s": 1.0}
    defaults.update(kwargs)
    return OpenRouteServiceClient(**defaults)


def sent_body(route) -> dict:
    return json.loads(route.calls[0].request.content)


def assert_authorized(route) -> None:
    assert route.calls[0].request.headers["Authorization"] == "key"


# ----------------------------------------------------------------------
# Directions
# ----------------------------------------------------------------------


@respx.mock
async def test_directions_geojson_sends_coordinates_and_options():
    url = f"{BASE_URL}/v2/directions/cycling-regular/geojson"
    route = respx.post(url).mock(return_value=httpx.Response(200, json=DIRECTIONS_PAYLOAD))

    result = await make_client().directions(
        "cycling-regular",
        [(10.5, 52.2), (10.55, 52.21)],
        options={"avoid_features": ["ferries"]},
        format="geojson",
    )

    assert result == DIRECTIONS_PAYLOAD
    assert sent_body(route) == {
        "coordinates": [[10.5, 52.2], [10.55, 52.21]],
        "options": {"avoid_features": ["ferries"]},
    }
    assert_authorized(route)


@respx.mock
async def test_directions_json_and_gpx_hit_format_specific_urls():
    gpx_xml = "<?xml version='1.0'?><gpx/>"
    json_route = respx.post(
        f"{BASE_URL}/v2/directions/cycling-road/json"
    ).mock(return_value=httpx.Response(200, json=DIRECTIONS_PAYLOAD))
    gpx_route = respx.post(
        f"{BASE_URL}/v2/directions/cycling-road/gpx"
    ).mock(return_value=httpx.Response(200, text=gpx_xml))

    client = make_client()
    coords = [(10.5, 52.2), (10.55, 52.21)]
    assert await client.directions("cycling-road", coords, format="json") == DIRECTIONS_PAYLOAD
    assert await client.directions("cycling-road", coords, format="gpx") == gpx_xml
    assert json_route.called and gpx_route.called


@respx.mock
async def test_directions_accepts_a_fully_built_body():
    url = f"{BASE_URL}/v2/directions/cycling-regular/geojson"
    route = respx.post(url).mock(return_value=httpx.Response(200, json=DIRECTIONS_PAYLOAD))
    body = {"coordinates": [[10.5, 52.2], [10.55, 52.21]], "elevation": True, "instructions": False}

    result = await make_client().directions("cycling-regular", body=body, format="geojson")

    assert result == DIRECTIONS_PAYLOAD
    assert sent_body(route) == body


@respx.mock
async def test_directions_requires_coordinates_or_body():
    with pytest.raises(ValueError):
        await make_client().directions("cycling-regular")


@respx.mock
async def test_directions_basic_uses_get_with_semicolon_coordinates():
    url = f"{BASE_URL}/v2/directions/cycling-regular"
    route = respx.get(url).mock(return_value=httpx.Response(200, json=DIRECTIONS_PAYLOAD))

    result = await make_client().directions_basic("cycling-regular", (10.5, 52.2), (10.55, 52.21))

    assert result == DIRECTIONS_PAYLOAD
    assert route.calls[0].request.url.params["coordinates"] == "10.5,52.2;10.55,52.21"
    assert_authorized(route)


# ----------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------


@respx.mock
async def test_export_json_uses_bare_endpoint_with_bbox():
    url = f"{BASE_URL}/v2/export/cycling-regular"
    route = respx.post(url).mock(return_value=httpx.Response(200, json={"points": [], "edges": []}))

    result = await make_client().export("cycling-regular", [10.5, 52.2, 10.6, 52.3])

    assert result == {"points": [], "edges": []}
    assert sent_body(route) == {"bbox": [10.5, 52.2, 10.6, 52.3]}


@respx.mock
async def test_export_topojson_uses_topojson_suffix_and_options():
    url = f"{BASE_URL}/v2/export/cycling-regular/topojson"
    route = respx.post(url).mock(return_value=httpx.Response(200, json={"type": "Topology"}))

    result = await make_client().export(
        "cycling-regular",
        [10.5, 52.2, 10.6, 52.3],
        options={"edge_based": True, "edge_properties": ["duration", "distance"]},
        format="topojson",
    )

    assert result == {"type": "Topology"}
    assert sent_body(route) == {
        "bbox": [10.5, 52.2, 10.6, 52.3],
        "options": {"edge_based": True, "edge_properties": ["duration", "distance"]},
    }


# ----------------------------------------------------------------------
# Isochrones
# ----------------------------------------------------------------------


@respx.mock
async def test_isochrones_time_ranges_on_bare_endpoint():
    url = f"{BASE_URL}/v2/isochrones/cycling-regular"
    route = respx.post(url).mock(
        return_value=httpx.Response(200, json={"type": "FeatureCollection"})
    )

    result = await make_client().isochrones("cycling-regular", [(10.5, 52.2)], ranges=[300, 600])

    assert result == {"type": "FeatureCollection"}
    assert sent_body(route) == {
        "locations": [[10.5, 52.2]],
        "range_type": "time",
        "range": [300.0, 600.0],
    }


@respx.mock
async def test_isochrones_distance_ranges_json_suffix():
    url = f"{BASE_URL}/v2/isochrones/cycling-regular/json"
    route = respx.post(url).mock(
        return_value=httpx.Response(200, json={"type": "FeatureCollection"})
    )

    await make_client().isochrones(
        "cycling-regular", [(10.5, 52.2)], ranges=[5000], range_type="distance", format="json"
    )

    body = sent_body(route)
    assert body["range_type"] == "distance"
    assert body["range"] == [5000.0]


# ----------------------------------------------------------------------
# Matrix
# ----------------------------------------------------------------------


@respx.mock
async def test_matrix_sends_sources_targets_and_outputs():
    url = f"{BASE_URL}/v2/matrix/cycling-regular"
    route = respx.post(url).mock(
        return_value=httpx.Response(200, json={"durations": [[0, 10], [10, 0]], "distances": []})
    )

    result = await make_client().matrix(
        "cycling-regular",
        [(10.5, 52.2), (10.55, 52.21)],
        sources=[0],
        targets=[1],
        outputs=["duration", "distance"],
    )

    assert result["durations"] == [[0, 10], [10, 0]]
    body = sent_body(route)
    assert body["locations"] == [[10.5, 52.2], [10.55, 52.21]]
    assert body["sources"] == [0]
    assert body["targets"] == [1]
    assert body["outputs"] == ["duration", "distance"]


@respx.mock
async def test_matrix_defaults_to_square_matrix_without_sources():
    url = f"{BASE_URL}/v2/matrix/cycling-regular"
    route = respx.post(url).mock(
        return_value=httpx.Response(200, json={"durations": [[0, 10], [10, 0]]})
    )

    await make_client().matrix("cycling-regular", [(10.5, 52.2), (10.55, 52.21)])

    body = sent_body(route)
    assert "sources" not in body
    assert "targets" not in body


# ----------------------------------------------------------------------
# Snapping
# ----------------------------------------------------------------------


@respx.mock
async def test_snap_geojson_sends_search_radius():
    url = f"{BASE_URL}/v2/snap/cycling-regular"
    route = respx.post(url).mock(
        return_value=httpx.Response(200, json={"type": "FeatureCollection"})
    )

    result = await make_client().snap("cycling-regular", [(10.5, 52.2)])

    assert result == {"type": "FeatureCollection"}
    assert sent_body(route) == {"locations": [[10.5, 52.2]], "options": {"search_radius": 200.0}}


@respx.mock
async def test_snap_json_suffix_merges_extra_options():
    url = f"{BASE_URL}/v2/snap/cycling-regular/json"
    route = respx.post(url).mock(return_value=httpx.Response(200, json={}))

    await make_client().snap(
        "cycling-regular", [(10.5, 52.2)], search_radius=50.0, options={"elevation": True},
        format="json",
    )

    assert sent_body(route) == {
        "locations": [[10.5, 52.2]],
        "options": {"search_radius": 50.0, "elevation": True},
    }


# ----------------------------------------------------------------------
# POIs
# ----------------------------------------------------------------------


@respx.mock
async def test_pois_sends_bbox_filter_and_options():
    url = f"{BASE_URL}/openpoiservice/v0/pois"
    route = respx.post(url).mock(
        return_value=httpx.Response(200, json={"type": "FeatureCollection"})
    )

    result = await make_client().pois(
        [10.5, 52.2, 10.6, 52.3],
        filter={"category": ["amenity=cafe"]},
        options={"limit": 100},
    )

    assert result == {"type": "FeatureCollection"}
    assert sent_body(route) == {
        "bbox": [10.5, 52.2, 10.6, 52.3],
        "filter": {"category": ["amenity=cafe"]},
        "options": {"limit": 100},
    }


# ----------------------------------------------------------------------
# Optimization (Vroom)
# ----------------------------------------------------------------------


@respx.mock
async def test_optimize_passes_vroom_payload_through():
    url = f"{BASE_URL}/vroom/v0"
    route = respx.post(url).mock(
        return_value=httpx.Response(200, json={"dispatch_status": "ok", "dispatch_time": 0.1})
    )
    payload = {
        "source": {"location": [10.5, 52.2]},
        "target": {"location": [10.55, 52.21]},
        "profile": "bike",
        "vehicles": [{"id": "v1", "profile": "bike", "capacity": 1}],
        "locations": [{"id": "l1", "location": [10.52, 52.2]}],
    }

    result = await make_client().optimize(payload)

    assert result["dispatch_status"] == "ok"
    assert sent_body(route) == payload


# ----------------------------------------------------------------------
# Elevation
# ----------------------------------------------------------------------


@respx.mock
async def test_elevation_line_geojson_in_and_out():
    url = f"{BASE_URL}/openelevationservice/v0/line"
    route = respx.post(url).mock(
        return_value=httpx.Response(200, json={"type": "LineString", "coordinates": []})
    )
    geometry = {
        "type": "LineString",
        "coordinates": [[13.349762, 38.112952], [12.638397, 37.645772]],
    }

    result = await make_client().elevation_line(geometry)

    assert result["type"] == "LineString"
    assert sent_body(route) == {
        "format_in": "geojson",
        "format_out": "geojson",
        "geometry": geometry,
    }


@respx.mock
async def test_elevation_line_polyline_in_returns_encoded_string():
    url = f"{BASE_URL}/openelevationservice/v0/line"
    route = respx.post(url).mock(return_value=httpx.Response(200, text="_p~iFpsuxRp_"))

    result = await make_client().elevation_line(
        [[13.349762, 38.112952], [12.638397, 37.645772]],
        format_in="polyline",
        format_out="encodedpolyline5",
    )

    assert result == "_p~iFpsuxRp_"
    assert sent_body(route) == {
        "format_in": "polyline",
        "format_out": "encodedpolyline5",
        "geometry": [[13.349762, 38.112952], [12.638397, 37.645772]],
    }


@respx.mock
async def test_elevation_point_get_sends_lon_lat_param():
    url = f"{BASE_URL}/openelevationservice/v0/point"
    route = respx.get(url).mock(return_value=httpx.Response(200, text="10.5,52.2,120.5"))

    result = await make_client().elevation_point((10.5, 52.2))

    assert result == "10.5,52.2,120.5"
    assert route.calls[0].request.url.params["geometry"] == "10.5,52.2"


@respx.mock
async def test_elevation_point_post_geojson():
    url = f"{BASE_URL}/openelevationservice/v0/point"
    route = respx.post(url).mock(
        return_value=httpx.Response(200, json={"type": "Point", "coordinates": [10.5, 52.2, 120.5]})
    )
    geometry = {"type": "Point", "coordinates": [10.5, 52.2]}

    result = await make_client().elevation_point(
        geometry, method="post", format_in="geojson", format_out="geojson"
    )

    assert result["type"] == "Point"
    assert sent_body(route) == {
        "format_in": "geojson",
        "format_out": "geojson",
        "geometry": geometry,
    }


# ----------------------------------------------------------------------
# Geocoding (Pelias)
# ----------------------------------------------------------------------


@respx.mock
async def test_pelias_search_sends_text_and_omits_unset_boundaries():
    url = f"{BASE_URL}/pelias/v1/search"
    route = respx.get(url).mock(return_value=httpx.Response(200, json={"features": []}))

    result = await make_client().geocode_search("Wolfenbüttel")

    assert result == {"features": []}
    params = route.calls[0].request.url.params
    assert params["text"] == "Wolfenbüttel"
    assert params["size"] == "5"
    assert "boundary.countries" not in params


@respx.mock
async def test_pelias_search_includes_boundary_params_when_set():
    url = f"{BASE_URL}/pelias/v1/search"
    route = respx.get(url).mock(return_value=httpx.Response(200, json={"features": []}))

    await make_client().geocode_search(
        "Hannover", size=3, boundary_countries="DE", boundary_rect="9.5,52.1;10.5,52.5"
    )

    params = route.calls[0].request.url.params
    assert params["size"] == "3"
    assert params["boundary.countries"] == "DE"
    assert params["boundary.rect"] == "9.5,52.1;10.5,52.5"
    assert "boundary.geometries" not in params


@respx.mock
async def test_pelias_autocomplete():
    url = f"{BASE_URL}/pelias/v1/autocomplete"
    route = respx.get(url).mock(return_value=httpx.Response(200, json={"features": []}))

    result = await make_client().geocode_autocomplete("Wolf")

    assert result == {"features": []}
    assert route.calls[0].request.url.params["text"] == "Wolf"


@respx.mock
async def test_pelias_structured_search_sends_components():
    url = f"{BASE_URL}/pelias/v1/search/structured"
    route = respx.get(url).mock(return_value=httpx.Response(200, json={"features": []}))

    await make_client().geocode_search_structured(
        street="Steintor 1", locality="Hannover", country="DE", size=2
    )

    params = route.calls[0].request.url.params
    assert params["street"] == "Steintor 1"
    assert params["locality"] == "Hannover"
    assert params["country"] == "DE"
    assert params["size"] == "2"
    assert "region" not in params
    assert "county" not in params
    assert "postalcode" not in params


@respx.mock
async def test_pelias_reverse_formats_point_param():
    url = f"{BASE_URL}/pelias/v1/reverse"
    route = respx.get(url).mock(return_value=httpx.Response(200, json={"features": []}))

    result = await make_client().geocode_reverse((10.5, 52.2))

    assert result == {"features": []}
    assert route.calls[0].request.url.params["point"] == "10.5,52.2"


# ----------------------------------------------------------------------
# Error mapping
# ----------------------------------------------------------------------


@respx.mock
async def test_rate_limit_maps_to_provider_rate_limit_error():
    respx.post(f"{BASE_URL}/v2/directions/cycling-regular/geojson").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "5"})
    )

    with pytest.raises(ProviderRateLimitError) as exc_info:
        await make_client().directions("cycling-regular", [(10.5, 52.2), (10.55, 52.21)])
    assert exc_info.value.detail["retry_after"] == "5"


@respx.mock
async def test_ors_no_route_code_maps_to_no_route_error():
    respx.post(f"{BASE_URL}/v2/directions/cycling-regular/geojson").mock(
        return_value=httpx.Response(400, json=NO_ROUTE_BODY)
    )

    with pytest.raises(ProviderNoRouteError):
        await make_client().directions("cycling-regular", [(10.5, 52.2), (10.55, 52.21)])


@respx.mock
async def test_other_client_errors_map_to_bad_response():
    respx.post(f"{BASE_URL}/v2/isochrones/cycling-regular").mock(
        return_value=httpx.Response(400, json=BAD_REQUEST_BODY)
    )

    with pytest.raises(ProviderBadResponseError) as exc_info:
        await make_client().isochrones("cycling-regular", [(10.5, 52.2)], ranges=[300])
    assert exc_info.value.detail["status_code"] == 400


@respx.mock
async def test_5xx_retries_then_raises_unavailable():
    respx.post(f"{BASE_URL}/v2/matrix/cycling-regular").mock(
        side_effect=[httpx.Response(503), httpx.Response(503), httpx.Response(503)]
    )

    with pytest.raises(ProviderUnavailableError):
        await make_client(max_retries=2).matrix("cycling-regular", [(10.5, 52.2)])


@respx.mock
async def test_5xx_retries_until_success():
    route = respx.post(f"{BASE_URL}/v2/matrix/cycling-regular").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"durations": []})]
    )

    result = await make_client(max_retries=1).matrix("cycling-regular", [(10.5, 52.2)])

    assert result == {"durations": []}
    assert len(route.calls) == 2


@respx.mock
async def test_timeout_raises_provider_timeout_error():
    respx.post(f"{BASE_URL}/v2/directions/cycling-regular/geojson").mock(
        side_effect=httpx.TimeoutException("timed out")
    )

    with pytest.raises(ProviderTimeoutError):
        await make_client(max_retries=0).directions(
            "cycling-regular", [(10.5, 52.2), (10.55, 52.21)]
        )


@respx.mock
async def test_invalid_json_maps_to_bad_response():
    respx.post(f"{BASE_URL}/v2/directions/cycling-regular/geojson").mock(
        return_value=httpx.Response(200, text="not json")
    )

    with pytest.raises(ProviderBadResponseError):
        await make_client().directions("cycling-regular", [(10.5, 52.2), (10.55, 52.21)])


# ----------------------------------------------------------------------
# Health
# ----------------------------------------------------------------------


@respx.mock
async def test_health_ok_unknown_degraded_unavailable():
    url = f"{BASE_URL}/v2/health"

    respx.get(url).mock(return_value=httpx.Response(200))
    assert (await make_client().health())["status"] == "ok"

    respx.reset()
    respx.get(url).mock(return_value=httpx.Response(404))
    result = await make_client().health()
    assert result["status"] == "unknown"

    respx.reset()
    respx.get(url).mock(return_value=httpx.Response(503))
    result = await make_client().health()
    assert result["status"] == "degraded"
    assert result["status_code"] == 503

    respx.reset()
    respx.get(url).mock(side_effect=httpx.ConnectError("connection refused"))
    result = await make_client().health()
    assert result["status"] == "unavailable"
