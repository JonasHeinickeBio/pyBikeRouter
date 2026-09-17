import httpx
import pytest
import respx

from bike_routing_agent.errors import GeocodingNotFoundError, ProviderBadResponseError
from bike_routing_agent.providers.geocoder import NominatimGeocoder

BASE_URL = "https://nominatim.openstreetmap.org"
URL = f"{BASE_URL}/search"


def make_geocoder() -> NominatimGeocoder:
    return NominatimGeocoder(base_url=BASE_URL, user_agent="test-agent/1.0", timeout_s=1.0)


@respx.mock
async def test_geocode_returns_ranked_candidates(nominatim_single_response):
    respx.get(URL).mock(return_value=httpx.Response(200, json=nominatim_single_response))
    geocoder = make_geocoder()

    candidates = await geocoder.geocode("Braunschweig Hauptbahnhof")

    assert len(candidates) == 2
    assert candidates[0].confidence >= candidates[1].confidence
    assert candidates[0].coordinate.lat == pytest.approx(52.2689)


@respx.mock
async def test_geocode_raises_not_found_on_empty_results():
    respx.get(URL).mock(return_value=httpx.Response(200, json=[]))
    geocoder = make_geocoder()

    with pytest.raises(GeocodingNotFoundError):
        await geocoder.geocode("nonexistent place xyz")


@respx.mock
async def test_geocode_raises_on_timeout():
    respx.get(URL).mock(side_effect=httpx.TimeoutException("timed out"))
    geocoder = make_geocoder()

    with pytest.raises(ProviderBadResponseError):
        await geocoder.geocode("Braunschweig")


@respx.mock
async def test_geocode_returns_multiple_candidates_for_ambiguous_query(
    nominatim_ambiguous_response,
):
    respx.get(URL).mock(return_value=httpx.Response(200, json=nominatim_ambiguous_response))
    geocoder = make_geocoder()

    candidates = await geocoder.geocode("Springfield")

    assert len(candidates) == 3
    labels = {c.label for c in candidates}
    assert any("Illinois" in label for label in labels)
    assert any("Missouri" in label for label in labels)


@respx.mock
async def test_geocode_uses_cache_on_second_call(nominatim_single_response):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=nominatim_single_response))
    geocoder = make_geocoder()

    await geocoder.geocode("Braunschweig Hauptbahnhof")
    await geocoder.geocode("Braunschweig Hauptbahnhof")

    assert route.call_count == 1
