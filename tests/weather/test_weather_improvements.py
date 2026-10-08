"""Feels-like, where the rain is, and sunrise/sunset (computed) -- pure functions."""

from datetime import UTC, date, datetime, timedelta

import pytest

from bike_routing_agent.weather.analysis import (
    advisories,
    build_route_weather,
    sample_route,
    summarize,
    wet_stretch,
)
from bike_routing_agent.weather.daylight import local_solar_date, sun_times
from bike_routing_agent.weather.models import HourlyWeather, WeatherSample

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
ROUTE = {"type": "LineString", "coordinates": [[10.0, 52.0], [10.0, 52.5]]}  # about 55 km


def hour(offset_h=0, **kw) -> HourlyWeather:
    return HourlyWeather(time=T0 + timedelta(hours=offset_h), **kw)


def sample(fraction, **kw) -> WeatherSample:
    return WeatherSample(
        fraction=fraction,
        lon=10,
        lat=52,
        time=T0 + timedelta(hours=fraction * 3),
        weather=hour(**kw),
    )


def minutes_apart(a: datetime, b: datetime) -> float:
    return abs((a - b).total_seconds()) / 60


# -- sunrise / sunset --


def test_sun_times_match_published_values():
    # London, summer solstice: sunrise 03:43 and sunset 20:21 UTC; winter solstice 08:04 / 15:53.
    sunrise, sunset = sun_times(date(2026, 6, 21), 51.5, -0.12)
    assert minutes_apart(sunrise, datetime(2026, 6, 21, 3, 43, tzinfo=UTC)) < 3
    assert minutes_apart(sunset, datetime(2026, 6, 21, 20, 21, tzinfo=UTC)) < 3
    sunrise, sunset = sun_times(date(2026, 12, 21), 51.5, -0.12)
    assert minutes_apart(sunrise, datetime(2026, 12, 21, 8, 4, tzinfo=UTC)) < 3
    assert minutes_apart(sunset, datetime(2026, 12, 21, 15, 53, tzinfo=UTC)) < 3


def test_equinox_day_is_about_twelve_hours_at_the_equator():
    sunrise, sunset = sun_times(date(2026, 3, 20), 0.0, 0.0)
    assert 12.0 < (sunset - sunrise).total_seconds() / 3600 < 12.3


def test_no_sunrise_in_polar_day_or_night():
    assert sun_times(date(2026, 6, 21), 78.2, 15.6) is None  # midnight sun
    assert sun_times(date(2026, 12, 21), 78.2, 15.6) is None  # polar night


def test_the_solar_day_follows_longitude_not_the_utc_date():
    late_utc = datetime(2026, 10, 7, 20, 0, tzinfo=UTC)
    assert local_solar_date(late_utc, 0.0) == date(2026, 10, 7)
    assert local_solar_date(late_utc, 150.0) == date(2026, 10, 8)  # already tomorrow in Asia
    assert local_solar_date(late_utc, -150.0) == date(2026, 10, 7)


# -- where the rain is --


def test_wet_stretch_spans_the_first_to_the_last_wet_sample():
    samples = [
        sample(0.0, precipitation_probability=10),
        sample(0.25, precipitation_probability=80),
        sample(0.5, precipitation_mm=1.2),
        sample(0.75, condition="rain"),
        sample(1.0, precipitation_probability=5),
    ]
    stretch = wet_stretch(samples, distance_m=40_000)
    assert (stretch.from_fraction, stretch.to_fraction) == (0.25, 0.75)
    assert (stretch.from_km, stretch.to_km) == (10.0, 30.0)
    assert stretch.from_time == samples[1].time and stretch.to_time == samples[3].time
    assert stretch.whole_route is False


def test_a_dry_forecast_has_no_wet_stretch_and_unknown_values_are_not_wet():
    assert wet_stretch([sample(0.0), sample(1.0, precipitation_probability=59)], 10_000) is None


def test_wet_stretch_without_a_route_length_has_no_kilometres():
    stretch = wet_stretch([sample(0.0, condition="drizzle"), sample(1.0, condition="rain")], None)
    assert stretch.from_km is None and stretch.whole_route is True


@pytest.mark.parametrize(
    ("samples", "length", "fragment"),
    [
        (
            [sample(0, precipitation_probability=0), sample(0.5, condition="rain"), sample(1.0)],
            40_000,
            "around km 20",
        ),
        (
            [sample(0.0), sample(0.25, condition="rain"), sample(0.75, condition="rain")],
            40_000,
            "between about km 10 and km 30",
        ),
        ([sample(0, condition="rain"), sample(1, condition="rain")], 40_000, "whole route"),
        ([sample(0.0), sample(0.5, condition="rain")], None, "along part of the route"),
    ],
)
def test_the_rain_advisory_says_where(samples, length, fragment):
    notes = advisories(summarize(samples, distance_m=length))
    assert any(fragment in n for n in notes), notes


def test_dry_weather_has_no_precipitation_note():
    assert not any("Precipitation" in n for n in advisories(summarize([sample(0), sample(1)])))


# -- feels like --


def test_feels_like_is_summarised_and_missing_stays_missing():
    samples = [
        sample(0, temperature_c=6, apparent_temperature_c=2),
        sample(1, temperature_c=8, apparent_temperature_c=4),
    ]
    summary = summarize(samples)
    assert (summary.apparent_temperature_min_c, summary.apparent_temperature_max_c) == (2, 4)
    none = summarize([sample(0, temperature_c=6)])
    assert none.apparent_temperature_min_c is None


def test_feels_like_is_mentioned_only_when_it_differs_and_is_cold():
    cold = summarize([sample(0, temperature_c=6, apparent_temperature_c=1)])
    assert any("feels like 1 °C" in n for n in advisories(cold))
    similar = summarize([sample(0, temperature_c=6, apparent_temperature_c=5)])
    assert not any("feels like" in n for n in advisories(similar))
    mild = summarize([sample(0, temperature_c=20, apparent_temperature_c=14)])
    assert not any("feels like" in n for n in advisories(mild))  # chilly, not cold


# -- daylight around the ride --


def weather_for(departure, duration_s, series_hour=0, lat=52.0, lon=10.0):
    route = {"type": "LineString", "coordinates": [[lon, lat], [lon, lat + 0.5]]}
    points = sample_route(route, max_samples=2, spacing_km=100)
    series = [
        [HourlyWeather(time=departure + timedelta(hours=series_hour), is_day=True)] for _ in points
    ]
    return build_route_weather(
        points,
        series,
        departure=departure,
        duration_s=duration_s,
        duration_source="provider",
        provider="p",
        attribution="a",
        retrieved_at=T0,
        distance_m=55_000,
    )


def test_arriving_after_sunset_is_stated_in_minutes():
    weather = weather_for(T0, 5.5 * 3600)  # leaves 12:00 UTC, arrives 17:30; sunset ~16:46 UTC
    assert weather.daylight is not None
    assert -60 < weather.daylight.minutes_of_light_left_at_arrival < -30
    assert any("after sunset" in n for n in weather.advisories)
    # the generic forecast-flag note is not repeated when sunset is known
    assert not any("after dark" in n for n in weather.advisories)


def test_sunset_soon_after_arrival_is_mentioned():
    weather = weather_for(T0, 4 * 3600 + 20 * 60)  # arrives ~16:20, sunset ~16:46
    assert 0 < weather.daylight.minutes_of_light_left_at_arrival <= 45
    assert any("Sunset is about" in n for n in weather.advisories)


def test_a_midday_ride_says_nothing_about_daylight():
    weather = weather_for(T0 - timedelta(hours=2), 2 * 3600)
    assert weather.daylight is not None
    assert not any("sunset" in n.lower() or "sunrise" in n.lower() for n in weather.advisories)


def test_starting_before_sunrise_is_stated():
    weather = weather_for(datetime(2026, 10, 8, 4, 0, tzinfo=UTC), 3600)
    assert weather.daylight.minutes_before_sunrise_at_start > 0
    assert any("before sunrise" in n for n in weather.advisories)


def test_polar_days_fall_back_to_the_forecast_daylight_flag():
    june = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    weather = weather_for(june, 3600, lat=78.2, lon=15.6)
    assert weather.daylight is None
    summary = summarize([sample(0, is_day=False)])
    assert any("after dark" in n for n in advisories(summary, None))
