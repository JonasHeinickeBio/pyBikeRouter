"""DWD (Bright Sky) adapter against a response captured from the real API, plus the
service-level rules that make it Germany-only with fallback."""

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
from bike_routing_agent.weather.dwd import DwdProvider, condition_from_record, in_germany
from bike_routing_agent.weather.models import HourlyWeather
from bike_routing_agent.weather.service import WeatherService

BRIGHT_SKY = "https://brightsky.example/weather"
FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "dwd_brightsky_response.json").read_text()
)
START = datetime(2026, 10, 8, 11, 0, tzinfo=UTC)
END = START + timedelta(hours=7)
BRAUNSCHWEIG = (10.52, 52.27)
WOLFENBUETTEL = (10.53, 52.16)


def provider(**kw) -> DwdProvider:
    return DwdProvider(base_url=BRIGHT_SKY, **kw)


@respx.mock
async def test_parses_a_real_response_into_hourly_weather():
    route = respx.get(BRIGHT_SKY).mock(return_value=httpx.Response(200, json=FIXTURE))
    [series] = await provider().forecast([BRAUNSCHWEIG], START, END)
    assert len(series) == len(FIXTURE["weather"]) == 8
    first = series[0]
    raw = FIXTURE["weather"][0]
    assert first.time == datetime.fromisoformat(raw["timestamp"])
    assert first.temperature_c == raw["temperature"]
    assert (
        first.wind_speed_kmh == raw["wind_speed"] and first.wind_from_deg == raw["wind_direction"]
    )
    assert first.wind_gust_kmh == raw["wind_gust_speed"]
    assert first.precipitation_mm == raw["precipitation"]
    assert first.precipitation_probability == raw["precipitation_probability"]
    # what DWD does not publish stays missing, never zero
    assert first.uv_index is None and first.apparent_temperature_c is None
    request = route.calls.last.request
    assert request.url.params["lat"] == "52.2700" and request.url.params["lon"] == "10.5200"
    assert request.url.params["units"] == "dwd" and request.url.params["tz"] == "UTC"
    assert request.url.params["date"] == "2026-10-08T11:00"
    assert request.url.params["last_date"] == "2026-10-08T18:00"
    assert request.headers["user-agent"] == "bike-routing-agent"


@respx.mock
async def test_one_request_per_point_in_the_order_given():
    respx.get(BRIGHT_SKY).mock(return_value=httpx.Response(200, json=FIXTURE))
    series = await provider().forecast([BRAUNSCHWEIG, WOLFENBUETTEL], START, END)
    assert len(series) == 2 and len(respx.calls) == 2


def test_rain_beats_the_cloud_icon_and_dry_uses_the_icon():
    # real data: condition "rain" with a "cloudy" icon -> rain
    assert condition_from_record({"condition": "rain", "icon": "cloudy"}) == "rain"
    assert condition_from_record({"condition": "dry", "icon": "clear-night"}) == "clear"
    assert (
        condition_from_record({"condition": "dry", "icon": "partly-cloudy-day"}) == "partly_cloudy"
    )
    assert condition_from_record({"condition": None, "icon": "cloudy"}) == "cloudy"
    assert condition_from_record({"condition": "dry"}) == "clear"
    assert condition_from_record({"condition": "hail"}) == "thunderstorm"
    assert condition_from_record({"condition": "sleet"}) == "snow"
    assert condition_from_record({"condition": "fog", "icon": "fog"}) == "fog"
    assert condition_from_record({}) == "unknown"


@respx.mock
async def test_day_and_night_come_from_the_icon_and_bad_values_become_missing():
    payload = {
        "weather": [
            {"timestamp": "2026-10-08T12:00:00+00:00", "icon": "clear-day", "temperature": 10},
            {"timestamp": "2026-10-08T13:00:00+00:00", "icon": "clear-night", "wind_speed": -4},
            {
                "timestamp": "2026-10-08T14:00:00+00:00",
                "icon": "cloudy",
                "precipitation_probability": 140,
            },
            {"timestamp": "garbage"},
            {"no": "timestamp"},
        ]
    }
    respx.get(BRIGHT_SKY).mock(return_value=httpx.Response(200, json=payload))
    [series] = await provider().forecast([BRAUNSCHWEIG], START, END)
    assert [h.is_day for h in series] == [True, False, None]
    assert series[1].wind_speed_kmh is None  # negative is nonsense: missing
    assert series[2].precipitation_probability is None  # >100 is nonsense: missing


@respx.mock
@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(429), ProviderRateLimitError),
        (httpx.Response(503), ProviderUnavailableError),
        (httpx.Response(404, json={"detail": "no station"}), ProviderBadResponseError),
        (httpx.Response(200, text="not json"), ProviderBadResponseError),
        (httpx.Response(200, json={"weather": "x"}), ProviderBadResponseError),
        (httpx.Response(200, json=[1, 2]), ProviderBadResponseError),
    ],
)
async def test_failures_map_to_provider_errors(response, error):
    respx.get(BRIGHT_SKY).mock(return_value=response)
    with pytest.raises(error):
        await provider().forecast([BRAUNSCHWEIG], START, END)


@respx.mock
async def test_timeouts_and_connection_errors():
    respx.get(BRIGHT_SKY).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderTimeoutError):
        await provider().forecast([BRAUNSCHWEIG], START, END)
    respx.get(BRIGHT_SKY).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(ProviderUnavailableError):
        await provider().forecast([BRAUNSCHWEIG], START, END)


@respx.mock
async def test_health_levels():
    respx.get(BRIGHT_SKY).mock(return_value=httpx.Response(200, json=FIXTURE))
    assert await provider().health() == {"status": "ok"}
    respx.get(BRIGHT_SKY).mock(return_value=httpx.Response(429))
    assert (await provider().health())["status"] == "degraded"
    respx.get(BRIGHT_SKY).mock(return_value=httpx.Response(503))
    assert (await provider().health())["status"] == "unavailable"


def test_only_germany_is_covered():
    assert in_germany(*BRAUNSCHWEIG) and in_germany(13.4, 52.5) and in_germany(9.0, 48.0)
    assert not in_germany(2.35, 48.85)  # Paris
    assert not in_germany(-1.25, 51.75)  # Oxford
    p = provider()
    assert p.covers([BRAUNSCHWEIG, WOLFENBUETTEL])
    assert not p.covers([BRAUNSCHWEIG, (2.35, 48.85)])  # one point outside: not answered
    assert not p.covers([])


class _Fake:
    def __init__(self, name, covers=None, fail=False):
        self.name, self.attribution, self.fail, self.calls = name, name, fail, 0
        if covers is not None:
            self.covers = covers

    async def forecast(self, points, start, end):
        self.calls += 1
        if self.fail:
            raise ProviderUnavailableError("down", provider=self.name)
        return [[HourlyWeather(time=start)] for _ in points]

    async def health(self):
        return {"status": "ok"}


async def test_the_service_skips_a_provider_that_does_not_cover_the_route():
    dwd = _Fake("dwd", covers=lambda pts: False)
    other = _Fake("open-meteo")
    forecast = await WeatherService([dwd, other]).forecast([(2.35, 48.85)], START, END)
    assert forecast.provider == "open-meteo" and dwd.calls == 0


async def test_the_covering_provider_answers_first_and_falls_back_on_failure():
    dwd = _Fake("dwd", covers=lambda pts: True)
    other = _Fake("open-meteo")
    assert (
        await WeatherService([dwd, other]).forecast([BRAUNSCHWEIG], START, END)
    ).provider == "dwd"
    broken = _Fake("dwd", covers=lambda pts: True, fail=True)
    assert (
        await WeatherService([broken, other]).forecast([BRAUNSCHWEIG], START, END)
    ).provider == "open-meteo"


async def test_nothing_covering_the_route_is_reported_as_such():
    only_dwd = _Fake("dwd", covers=lambda pts: False)
    with pytest.raises(ProviderUnavailableError, match="covers"):
        await WeatherService([only_dwd]).forecast([(2.35, 48.85)], START, END)
