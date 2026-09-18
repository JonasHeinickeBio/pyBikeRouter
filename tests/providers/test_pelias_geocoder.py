"""Unit tests for the Pelias geocoding adapter (mocked HTTP via respx)."""

import httpx
import pytest
import respx

from bike_routing_agent.errors import (
    GeocodingNotFoundError,
    ProviderBadResponseError,
    ProviderRateLimitError,
)
from bike_routing_agent.providers.ors_client import OpenRouteServiceClient
from bike_routing_agent.providers.pelias import PeliasGeocoder

BASE_URL = "https://ors.example.org"
SEARCH_URL = f"{BASE_URL}/pelias/v1/search"


def make_geocoder(**kwargs) -> PeliasGeocoder:
    client = OpenRouteServiceClient(api_key="key", base_url=BASE_URL, timeout_s=1.0)
    return PeliasGeocoder(client=client, **kwargs)


def pelias_response(features: list[dict]) -> dict:
    return {
        "status": "OK",
        "version": "v1",
        "weasel": {"query": "test"},
        "total": len(features),
        "features": features,
    }


def feature(
    lon: float, lat: float, *, label: str | None = None, name: str | None = None,
    confidence: float = 0.8,
) -> dict:
    properties: dict = {"confidence": confidence}
    if label is not None:
        properties["label"] = label
    if name is not None:
        properties["name"] = name
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": properties,
    }


# ----------------------------------------------------------------------
# Normalization
# ----------------------------------------------------------------------


@respx.mock
async def test_geocode_normalizes_and_sorts_by_confidence():
    payload = pelias_response(
        [
            feature(10.5, 52.2, label="Berlin, Germany", confidence=0.7),
            feature(13.4, 52.5, label="Berlin-Brandenburg", confidence=0.95),
        ]
    )
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))

    candidates = await make_geocoder().geocode("Berlin", limit=2)

    assert [c.label for c in candidates] == ["Berlin-Brandenburg", "Berlin, Germany"]
    assert candidates[0].coordinate.lon == 13.4
    assert candidates[0].coordinate.lat == 52.5
    assert candidates[1].coordinate.lon == 10.5
    assert candidates[1].coordinate.lat == 52.2
    assert all(c.source == "pelias" for c in candidates)


@respx.mock
async def test_geocode_label_falls_back_to_name():
    payload = pelias_response([feature(1.0, 2.0, name="OnlyName")])
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))

    candidates = await make_geocoder().geocode("x")

    assert candidates[0].label == "OnlyName"


@respx.mock
async def test_geocode_clamps_confidence_into_unit_range():
    payload = pelias_response([feature(1.0, 2.0, confidence=1.7)])
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))

    candidates = await make_geocoder().geocode("x")

    assert candidates[0].confidence == 1.0


# ----------------------------------------------------------------------
# Request shape
# ----------------------------------------------------------------------


@respx.mock
async def test_geocode_sends_query_size_and_auth_header():
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=pelias_response([feature(1.0, 2.0)]))
    )

    await make_geocoder().geocode("Hamburg", limit=3)

    request = route.calls[0].request
    assert request.url.params["text"] == "Hamburg"
    assert request.url.params["size"] == "3"
    assert request.headers["Authorization"] == "key"


@respx.mock
async def test_geocode_passes_boundary_defaults():
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=pelias_response([feature(1.0, 2.0)]))
    )

    geocoder = make_geocoder(
        boundary_countries="DE,FR",
        boundary_rect="6.0,47.0,10.0,55.0",
    )
    await geocoder.geocode("Main Street")

    params = route.calls[0].request.url.params
    assert params["boundary.countries"] == "DE,FR"
    assert params["boundary.rect"] == "6.0,47.0,10.0,55.0"
    assert "boundary.geometries" not in params


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------


@respx.mock
async def test_geocode_empty_features_raises_not_found():
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=pelias_response([])))

    with pytest.raises(GeocodingNotFoundError):
        await make_geocoder().geocode("nowhere")


@respx.mock
async def test_geocode_features_not_a_list_raises_bad_response():
    payload = {"status": "OK", "features": "nope", "total": 0}
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))

    with pytest.raises(ProviderBadResponseError):
        await make_geocoder().geocode("x")


@respx.mock
async def test_geocode_feature_without_geometry_raises_bad_response():
    broken = {"type": "Feature", "properties": {"label": "broken", "confidence": 0.5}}
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=pelias_response([broken])))

    with pytest.raises(ProviderBadResponseError):
        await make_geocoder().geocode("x")


@respx.mock
async def test_geocode_client_rate_limit_propagates():
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(429))

    with pytest.raises(ProviderRateLimitError):
        await make_geocoder().geocode("x")


# ----------------------------------------------------------------------
# Cache
# ----------------------------------------------------------------------


@respx.mock
async def test_geocode_second_call_served_from_cache():
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=pelias_response([feature(1.0, 2.0)]))
    )
    geocoder = make_geocoder()

    first = await geocoder.geocode("cached place")
    second = await geocoder.geocode("cached place")

    assert route.call_count == 1
    assert first == second
