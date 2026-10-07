"""Open-Meteo and MET Norway adapters against responses captured from the real APIs."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from bike_routing_agent.errors import (
    ProviderBadResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from bike_routing_agent.weather.met_no import MetNoProvider
from bike_routing_agent.weather.open_meteo import OpenMeteoProvider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
OPEN_METEO = "https://api.open-meteo.example/v1/forecast"
MET_NO = "https://api.met.example/compact"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


START = datetime(2026, 10, 6, 22, 0, tzinfo=UTC)
END = START + timedelta(hours=3)


# -- Open-Meteo ------------------------------------------------------------------------


@respx.mock
async def test_open_meteo_parses_a_multi_location_response():
    route = respx.get(OPEN_METEO).mock(
        return_value=httpx.Response(200, json=fixture("open_meteo_forecast_two_points.json"))
    )
    provider = OpenMeteoProvider(base_url=OPEN_METEO)

    series = await provider.forecast([(10.52, 52.27), (10.54, 52.17)], START, END)

    assert len(series) == 2 and all(len(s) == 4 for s in series)
    first = series[0][0]
    assert first.time == datetime(2026, 10, 6, 22, 0, tzinfo=UTC)
    assert first.temperature_c == 13.6 and first.wind_from_deg == 119
    assert first.wind_speed_kmh == 3.7 and first.wind_gust_kmh == 7.6
    assert first.condition == "partly_cloudy" and first.weather_code == 1
    assert first.is_day is False and first.precipitation_probability == 0

    params = route.calls.last.request.url.params
    assert params["latitude"] == "52.2700,52.1700" and params["longitude"] == "10.5200,10.5400"
    assert params["wind_speed_unit"] == "kmh" and params["timezone"] == "UTC"
    assert params["timeformat"] == "unixtime"
    assert (params["start_hour"], params["end_hour"]) == ("2026-10-06T22:00", "2026-10-07T01:00")
    assert "wind_direction_10m" in params["hourly"] and "uv_index" in params["hourly"]


@respx.mock
async def test_open_meteo_single_location_answers_with_an_object_not_a_list():
    respx.get(OPEN_METEO).mock(
        return_value=httpx.Response(200, json=fixture("open_meteo_forecast_single_point.json"))
    )
    series = await OpenMeteoProvider(base_url=OPEN_METEO).forecast([(10.52, 52.27)], START, END)
    assert len(series) == 1 and len(series[0]) == 4


@respx.mock
async def test_open_meteo_missing_values_stay_missing_and_garbage_is_dropped():
    payload = fixture("open_meteo_forecast_single_point.json")
    hourly = payload["hourly"]
    hourly["temperature_2m"][0] = None
    hourly["wind_speed_10m"][1] = -5  # impossible: reads as missing, not as -5
    hourly["precipitation_probability"][2] = 250  # impossible percentage
    hourly["weather_code"][3] = None
    respx.get(OPEN_METEO).mock(return_value=httpx.Response(200, json=payload))

    s = (await OpenMeteoProvider(base_url=OPEN_METEO).forecast([(10.52, 52.27)], START, END))[0]

    assert s[0].temperature_c is None
    assert s[1].wind_speed_kmh is None
    assert s[2].precipitation_probability is None
    assert s[3].condition == "unknown" and s[3].weather_code is None


@respx.mock
@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(429), ProviderRateLimitError),
        (httpx.Response(503), ProviderUnavailableError),
        (httpx.Response(400, json={"reason": "out of range"}), ProviderBadResponseError),
        (httpx.Response(200, text="<html>"), ProviderBadResponseError),
        (httpx.Response(200, json={"hourly": {}}), ProviderBadResponseError),
        (
            httpx.Response(200, json=[{"hourly": {"time": []}}, {"hourly": {"time": []}}]),
            ProviderBadResponseError,
        ),
    ],
)
async def test_open_meteo_failures_are_structured_provider_errors(response, error):
    respx.get(OPEN_METEO).mock(return_value=response)
    with pytest.raises(error):
        await OpenMeteoProvider(base_url=OPEN_METEO).forecast([(10.52, 52.27)], START, END)


@respx.mock
async def test_open_meteo_transport_errors_are_structured():
    respx.get(OPEN_METEO).mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(ProviderTimeoutError):
        await OpenMeteoProvider(base_url=OPEN_METEO).forecast([(1, 1)], START, END)
    respx.get(OPEN_METEO).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ProviderUnavailableError):
        await OpenMeteoProvider(base_url=OPEN_METEO).forecast([(1, 1)], START, END)


async def test_open_meteo_with_no_points_makes_no_request():
    assert await OpenMeteoProvider(base_url=OPEN_METEO).forecast([], START, END) == []


@respx.mock
async def test_open_meteo_health_levels():
    provider = OpenMeteoProvider(base_url=OPEN_METEO)
    respx.get(OPEN_METEO).mock(
        return_value=httpx.Response(200, json=fixture("open_meteo_forecast_single_point.json"))
    )
    assert await provider.health() == {"status": "ok"}
    respx.get(OPEN_METEO).mock(return_value=httpx.Response(429))
    assert (await provider.health())["status"] == "degraded"
    respx.get(OPEN_METEO).mock(side_effect=httpx.ConnectError("x"))
    assert await provider.health() == {"status": "unavailable"}
    respx.get(OPEN_METEO).mock(return_value=httpx.Response(200, text="nope"))
    assert await provider.health() == {"status": "degraded"}


async def test_open_meteo_uses_an_injected_client():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture("open_meteo_forecast_single_point.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenMeteoProvider(base_url=OPEN_METEO, client=client)
        assert len(await provider.forecast([(10.52, 52.27)], START, END)) == 1


def test_open_meteo_attribution_is_present():
    assert "Open-Meteo" in OpenMeteoProvider().attribution


# -- MET Norway -------------------------------------------------------------------------


@respx.mock
async def test_met_no_parses_the_compact_format_and_converts_units():
    route = respx.get(MET_NO).mock(
        return_value=httpx.Response(200, json=fixture("met_no_compact_response.json"))
    )
    provider = MetNoProvider(user_agent="tests/1.0 contact@example.org", base_url=MET_NO)

    (series,) = await provider.forecast([(10.5267123, 52.2689456)], START, END)

    request = route.calls.last.request
    assert request.headers["user-agent"] == "tests/1.0 contact@example.org"
    assert request.url.params["lat"] == "52.2689" and request.url.params["lon"] == "10.5267"
    # window = [start-1h, end+1h]; the far-future entry is excluded
    assert [h.time.hour for h in series] == [22, 23, 0, 1, 2]
    first = series[0]
    assert first.temperature_c == 12.5 and first.wind_from_deg == 109.0
    assert first.wind_speed_kmh == pytest.approx(1.7 * 3.6)  # m/s -> km/h
    assert first.condition == "clear" and first.is_day is False
    assert first.precipitation_mm == 0.0
    assert first.precipitation_probability is None and first.uv_index is None  # not in compact


@respx.mock
async def test_met_no_uses_the_six_hour_total_as_an_hourly_rate_when_needed():
    payload = fixture("met_no_compact_response.json")
    far = payload["properties"]["timeseries"][-1]
    far["data"]["next_6_hours"]["details"]["precipitation_amount"] = 3.0
    respx.get(MET_NO).mock(return_value=httpx.Response(200, json=payload))
    provider = MetNoProvider(user_agent="t/1", base_url=MET_NO)
    wide_end = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)

    (series,) = await provider.forecast([(10.5, 52.2)], wide_end - timedelta(hours=1), wide_end)

    assert series[-1].precipitation_mm == pytest.approx(0.5)  # 3 mm over 6 h


@respx.mock
async def test_met_no_queries_every_point():
    route = respx.get(MET_NO).mock(
        return_value=httpx.Response(200, json=fixture("met_no_compact_response.json"))
    )
    provider = MetNoProvider(user_agent="t/1", base_url=MET_NO)
    series = await provider.forecast([(10.5, 52.2), (10.6, 52.3), (10.7, 52.4)], START, END)
    assert len(series) == 3 and route.call_count == 3


@respx.mock
@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(403), ProviderBadResponseError),  # blocked User-Agent
        (httpx.Response(429), ProviderRateLimitError),
        (httpx.Response(500), ProviderUnavailableError),
        (httpx.Response(200, text="oops"), ProviderBadResponseError),
        (httpx.Response(200, json={"properties": {}}), ProviderBadResponseError),
        (httpx.Response(200, json={"properties": {"timeseries": "x"}}), ProviderBadResponseError),
    ],
)
async def test_met_no_failures_are_structured_provider_errors(response, error):
    respx.get(MET_NO).mock(return_value=response)
    with pytest.raises(error):
        await MetNoProvider(user_agent="t/1", base_url=MET_NO).forecast([(10.5, 52.2)], START, END)


@respx.mock
async def test_met_no_skips_malformed_entries_instead_of_failing():
    payload = fixture("met_no_compact_response.json")
    payload["properties"]["timeseries"].insert(0, {"time": "garbage", "data": {}})
    payload["properties"]["timeseries"].insert(1, {"nope": 1})
    respx.get(MET_NO).mock(return_value=httpx.Response(200, json=payload))
    (series,) = await MetNoProvider(user_agent="t/1", base_url=MET_NO).forecast(
        [(10.5, 52.2)], START, END
    )
    assert len(series) == 5


@respx.mock
async def test_met_no_transport_errors_and_health():
    provider = MetNoProvider(user_agent="t/1", base_url=MET_NO)
    respx.get(MET_NO).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderTimeoutError):
        await provider.forecast([(1, 1)], START, END)
    assert await provider.health() == {"status": "unavailable"}
    respx.get(MET_NO).mock(return_value=httpx.Response(429))
    assert (await provider.health())["status"] == "degraded"
    respx.get(MET_NO).mock(return_value=httpx.Response(200, json={}))
    assert (await provider.health())["status"] == "ok"  # it answered; parsing is not health's job
    respx.get(MET_NO).mock(return_value=httpx.Response(403))
    assert (await provider.health())["status"] == "degraded"
