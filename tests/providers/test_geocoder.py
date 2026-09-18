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
async def test_geocode_returns_cache_hit_without_second_request(nominatim_single_response):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=nominatim_single_response))
    geocoder = make_geocoder()

    await geocoder.geocode("Braunschweig Hauptbahnhof")
    await geocoder.geocode("Braunschweig Hauptbahnhof")

    assert route.call_count == 1


def address_hit(house_number: str = "23", importance: float = 0.0001) -> dict:
    return {
        "place_id": 1,
        "category": "address",
        "type": "place",
        "addresstype": "",
        "name": "",
        "display_name": (
            "23, Kasernenstraße, Östliches Ringgebiet, Braunschweig, "
            "Niedersachsen, 38106, Deutschland"
        ),
        "lat": "52.2695156",
        "lon": "10.5357469",
        "importance": importance,
        "address": {
            "house_number": house_number,
            "road": "Kasernenstraße",
            "postcode": "38106",
            "city": "Braunschweig",
        },
    }


@respx.mock
async def test_query_is_normalized_before_request():
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=[address_hit()]))
    geocoder = make_geocoder()

    await geocoder.geocode("Kasernenstr 23 in 38106 Braunschweig")

    assert route.calls[0].request.url.params["q"] == "Kasernenstraße 23, 38106 Braunschweig"


@respx.mock
async def test_exact_house_number_match_gets_confidence_floor():
    respx.get(URL).mock(return_value=httpx.Response(200, json=[address_hit()]))
    geocoder = make_geocoder()

    candidates = await geocoder.geocode("Kasernenstraße 23, 38106 Braunschweig")

    assert candidates[0].confidence == pytest.approx(0.9)


@respx.mock
async def test_house_number_mismatch_does_not_get_boost():
    respx.get(URL).mock(return_value=httpx.Response(200, json=[address_hit(house_number="23")]))
    geocoder = make_geocoder()

    candidates = await geocoder.geocode("Kasernenstraße 99, 38106 Braunschweig")

    assert candidates[0].confidence == pytest.approx(0.0001)


@respx.mock
async def test_unique_street_match_without_house_number_gets_moderate_floor():
    street = address_hit()
    street["category"] = "highway"
    street["address"] = {"road": "Kasernenstraße", "city": "Braunschweig"}
    respx.get(URL).mock(return_value=httpx.Response(200, json=[street]))
    geocoder = make_geocoder()

    candidates = await geocoder.geocode("Kasernenstraße Braunschweig")

    assert candidates[0].confidence == pytest.approx(0.6)


@respx.mock
async def test_structured_fallback_when_free_text_finds_nothing():
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(200, json=[]),
            httpx.Response(200, json=[address_hit()]),
        ]
    )
    geocoder = make_geocoder()

    candidates = await geocoder.geocode("Kasernenstr 23 in 38106 Braunschweig")

    assert route.call_count == 2
    second = route.calls[1].request.url.params
    assert second["street"] == "Kasernenstraße 23"
    assert second["postalcode"] == "38106"
    assert second["city"] == "Braunschweig"
    assert "q" not in second
    assert candidates[0].confidence == pytest.approx(0.9)


@respx.mock
async def test_structured_fallback_failure_still_raises_not_found():
    respx.get(URL).mock(return_value=httpx.Response(200, json=[]))
    geocoder = make_geocoder()

    with pytest.raises(GeocodingNotFoundError):
        await geocoder.geocode("Kasernenstr 23 in 38106 Braunschweig")
