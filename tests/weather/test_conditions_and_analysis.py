"""Weather conditions mapping and the route/wind analysis (pure functions)."""

import math
from datetime import UTC, datetime, timedelta

import pytest

from bike_routing_agent.weather.analysis import (
    advisories,
    build_route_weather,
    haversine_m,
    initial_bearing,
    line_coordinates,
    pick_hour,
    sample_route,
    summarize,
    wind_components,
)
from bike_routing_agent.weather.conditions import condition_from_met_symbol, condition_from_wmo
from bike_routing_agent.weather.models import HourlyWeather, WeatherSample, WeatherSummary

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def hour(offset_h=0, **kw) -> HourlyWeather:
    return HourlyWeather(time=T0 + timedelta(hours=offset_h), **kw)


def line(*coords):
    return {"type": "LineString", "coordinates": [list(c) for c in coords]}


# -- condition vocabularies --


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, "clear"),
        (1, "partly_cloudy"),
        (2, "partly_cloudy"),
        (3, "cloudy"),
        (45, "fog"),
        (53, "drizzle"),
        (61, "rain"),
        (81, "rain"),
        (66, "freezing_rain"),
        (73, "snow"),
        (86, "snow"),
        (95, "thunderstorm"),
        (99, "thunderstorm"),
        (42, "unknown"),
        (None, "unknown"),
    ],
)
def test_wmo_codes(code, expected):
    assert condition_from_wmo(code) == expected


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("clearsky_day", "clear"),
        ("fair_night", "partly_cloudy"),
        ("partlycloudy_polartwilight", "partly_cloudy"),
        ("cloudy", "cloudy"),
        ("fog", "fog"),
        ("lightrain", "rain"),
        ("heavyrainshowers_day", "rain"),
        ("sleet", "freezing_rain"),
        ("lightsnowandthunder", "thunderstorm"),
        ("snow", "snow"),
        ("rainandthunder", "thunderstorm"),
        ("something_new", "unknown"),
        (None, "unknown"),
        ("", "unknown"),
    ],
)
def test_met_symbols(symbol, expected):
    assert condition_from_met_symbol(symbol) == expected


# -- geometry --


def test_haversine_and_bearing_known_values():
    assert haversine_m((0, 0), (0, 1)) == pytest.approx(111_195, rel=1e-3)  # 1 degree of latitude
    assert initial_bearing((10, 52), (10, 53)) == pytest.approx(0, abs=0.01)  # north
    assert initial_bearing((10, 52), (11, 52)) == pytest.approx(90, abs=0.5)  # east
    assert initial_bearing((10, 52), (10, 51)) == pytest.approx(180, abs=0.01)
    assert initial_bearing((10, 52), (9, 52)) == pytest.approx(270, abs=0.5)


def test_line_coordinates_flattens_and_ignores_other_types():
    multi = {"type": "MultiLineString", "coordinates": [[[1, 2, 5], [3, 4]], [[5, 6]]]}
    assert line_coordinates(multi) == [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]
    assert line_coordinates({"type": "Point", "coordinates": [1, 2]}) == []
    assert line_coordinates({}) == []


def test_samples_are_evenly_spaced_with_start_and_end_and_a_cap():
    north = line((10.0, 52.0), (10.0, 52.5))  # ~55.6 km
    points = sample_route(north, max_samples=5, spacing_km=10)
    assert [round(p.fraction, 2) for p in points] == [0, 0.25, 0.5, 0.75, 1.0]
    assert points[0].lat == pytest.approx(52.0) and points[-1].lat == pytest.approx(52.5)
    assert all(p.bearing_deg == pytest.approx(0, abs=0.1) for p in points)
    assert len(sample_route(north, max_samples=3, spacing_km=1)) == 3


def test_a_short_route_still_gets_start_and_end():
    points = sample_route(line((10.0, 52.0), (10.0, 52.01)), max_samples=5, spacing_km=10)
    assert [p.fraction for p in points] == [0.0, 1.0]


def test_the_bearing_follows_the_route_around_a_corner():
    # 20 km north, then 20 km east
    corner = line((10.0, 52.0), (10.0, 52.18), (10.29, 52.18))
    bearings = [round(p.bearing_deg) for p in sample_route(corner, max_samples=5, spacing_km=10)]
    assert bearings[0] == 0 and bearings[-1] == pytest.approx(90, abs=2)


@pytest.mark.parametrize("geometry", [{}, line((1, 2)), line((1, 2), (1, 2)), {"type": "Point"}])
def test_unusable_geometry_has_no_samples(geometry):
    assert sample_route(geometry) == []


# -- wind relative to travel --


@pytest.mark.parametrize(
    ("from_deg", "bearing", "head", "cross"),
    [
        (0, 0, 20, 0),  # heading north into a north wind: pure headwind
        (180, 0, -20, 0),  # same wind from the south: pure tailwind
        (90, 0, 0, 20),  # heading north, wind from the east (right): crosswind from the right
        (270, 0, 0, -20),  # from the west (left)
        (45, 0, 20 * math.cos(math.radians(45)), 20 * math.sin(math.radians(45))),
        (
            350,
            10,
            20 * math.cos(math.radians(20)),
            -20 * math.sin(math.radians(20)),
        ),  # wraps around north
    ],
)
def test_wind_components(from_deg, bearing, head, cross):
    h, c = wind_components(20, from_deg, bearing)
    assert h == pytest.approx(head, abs=1e-9) and c == pytest.approx(cross, abs=1e-9)


# -- forecast hours --


def test_pick_hour_takes_the_nearest_hour_within_reach():
    series = [hour(0), hour(1), hour(2)]
    assert pick_hour(series, T0 + timedelta(minutes=20)) is series[0]
    assert pick_hour(series, T0 + timedelta(minutes=40)) is series[1]
    assert pick_hour(series, T0 + timedelta(hours=2, minutes=80)) is series[2]  # 80 min: in reach
    assert pick_hour(series, T0 + timedelta(hours=2, minutes=100)) is None  # 100 min: not covered
    assert pick_hour(series, T0 + timedelta(hours=5)) is None
    assert pick_hour([], T0) is None


# -- summary and advisories --


def sample(fraction, weather, headwind=None):
    return WeatherSample(
        fraction=fraction,
        lon=10,
        lat=52,
        time=weather.time,
        bearing_deg=0,
        weather=weather,
        headwind_kmh=headwind,
        crosswind_kmh=0,
    )


def test_summary_reports_ranges_means_and_worst_condition():
    samples = [
        sample(
            0,
            hour(
                0,
                temperature_c=8,
                precipitation_probability=10,
                wind_speed_kmh=10,
                wind_gust_kmh=20,
                condition="cloudy",
                uv_index=1,
                is_day=True,
            ),
            12,
        ),
        sample(
            1,
            hour(
                1,
                temperature_c=14,
                precipitation_probability=70,
                precipitation_mm=1.4,
                wind_speed_kmh=30,
                wind_gust_kmh=45,
                condition="rain",
                uv_index=3,
                is_day=False,
            ),
            -14,
        ),
    ]
    s = summarize(samples)
    assert (s.temperature_min_c, s.temperature_max_c) == (8, 14)
    assert s.precipitation_probability_max == 70 and s.precipitation_mm_per_hour_max == 1.4
    assert (s.wind_speed_mean_kmh, s.wind_speed_max_kmh, s.wind_gust_max_kmh) == (20, 30, 45)
    assert s.headwind_mean_kmh == pytest.approx(-1)
    assert (s.headwind_share, s.tailwind_share) == (0.5, 0.5)
    assert s.uv_index_max == 3 and s.worst_condition == "rain" and s.after_dark is True


def test_missing_values_stay_missing_not_zero():
    s = summarize([sample(0, hour(0, condition="unknown"))])
    assert s.temperature_min_c is None and s.wind_speed_mean_kmh is None
    assert s.headwind_mean_kmh is None and s.headwind_share is None and s.after_dark is None
    assert s.worst_condition == "unknown"


def test_a_calm_mild_day_has_no_advisories():
    assert (
        advisories(
            WeatherSummary(
                temperature_min_c=14,
                temperature_max_c=18,
                precipitation_probability_max=10,
                wind_speed_max_kmh=12,
                wind_gust_max_kmh=20,
                headwind_mean_kmh=3,
                worst_condition="clear",
                after_dark=False,
            )
        )
        == []
    )


@pytest.mark.parametrize(
    ("summary", "fragment"),
    [
        (WeatherSummary(worst_condition="thunderstorm"), "Thunderstorms"),
        (WeatherSummary(worst_condition="snow"), "Snow or freezing rain"),
        (WeatherSummary(worst_condition="freezing_rain"), "Snow or freezing rain"),
        (WeatherSummary(worst_condition="fog"), "Fog"),
        (WeatherSummary(precipitation_probability_max=80), "Rain is likely: up to 80% chance"),
        (WeatherSummary(precipitation_mm_per_hour_max=2.5), "up to 2.5 mm/h"),
        (WeatherSummary(wind_gust_max_kmh=62), "gusts up to 62 km/h"),
        (WeatherSummary(wind_speed_max_kmh=34), "Sustained wind up to 34 km/h"),
        (WeatherSummary(headwind_mean_kmh=18), "18 km/h of headwind"),
        (WeatherSummary(headwind_mean_kmh=-19), "19 km/h of tailwind"),
        (WeatherSummary(temperature_min_c=-2), "at or below freezing"),
        (WeatherSummary(temperature_min_c=2), "down to 2 °C"),
        (WeatherSummary(temperature_max_c=34), "up to 34 °C"),
        (WeatherSummary(uv_index_max=7), "UV index up to 7"),
        (WeatherSummary(after_dark=True), "after dark"),
    ],
)
def test_advisories_state_forecast_facts(summary, fragment):
    notes = " ".join(advisories(summary))
    assert fragment.lower() in notes.lower()
    assert "safe" not in notes.lower()


def test_a_thunderstorm_is_not_also_reported_as_plain_rain():
    notes = advisories(
        WeatherSummary(worst_condition="thunderstorm", precipitation_probability_max=90)
    )
    assert len(notes) == 1 and "Thunderstorms" in notes[0]


# -- the whole thing --


def test_build_route_weather_times_each_sample_along_the_ride():
    points = sample_route(line((10.0, 52.0), (10.0, 52.5)), max_samples=3, spacing_km=10)
    series = [
        [hour(0, wind_speed_kmh=10, wind_from_deg=0)],
        [hour(1, wind_speed_kmh=10, wind_from_deg=0)],
        [hour(2, wind_speed_kmh=10, wind_from_deg=0)],
    ]
    weather = build_route_weather(
        points,
        series,
        departure=T0,
        duration_s=7200,
        duration_source="provider",
        provider="p",
        attribution="a",
        retrieved_at=T0,
    )
    assert weather is not None
    assert [s.time for s in weather.samples] == [
        T0,
        T0 + timedelta(hours=1),
        T0 + timedelta(hours=2),
    ]
    assert weather.arrival == T0 + timedelta(hours=2)
    # riding north into a north wind: the full wind is headwind at every sample
    assert all(s.headwind_kmh == pytest.approx(10) for s in weather.samples)
    assert weather.summary.headwind_share == 1.0 and weather.duration_source == "provider"


def test_samples_the_forecast_does_not_cover_are_dropped_and_no_coverage_is_none():
    points = sample_route(line((10.0, 52.0), (10.0, 52.5)), max_samples=3, spacing_km=10)
    covered_first_only = [[hour(0)], [hour(-10)], [hour(-10)]]
    weather = build_route_weather(
        points,
        covered_first_only,
        departure=T0,
        duration_s=7200,
        duration_source="estimated",
        provider="p",
        attribution="a",
        retrieved_at=T0,
    )
    assert (
        weather is not None and len(weather.samples) == 1 and weather.duration_source == "estimated"
    )
    nothing = build_route_weather(
        points,
        [[], [], []],
        departure=T0,
        duration_s=7200,
        duration_source="provider",
        provider="p",
        attribution="a",
        retrieved_at=T0,
    )
    assert nothing is None
