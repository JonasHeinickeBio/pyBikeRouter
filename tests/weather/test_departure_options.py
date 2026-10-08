"""Comparing departure times around the requested one (pure functions and the node)."""

from datetime import UTC, datetime, timedelta

from bike_routing_agent.weather.analysis import (
    build_route_weather,
    departure_options,
    sample_route,
    suggest_departure,
)
from bike_routing_agent.weather.models import HourlyWeather

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
ROUTE = {"type": "LineString", "coordinates": [[10.0, 52.0], [10.0, 52.5]]}  # about 55 km
POINTS = sample_route(ROUTE, max_samples=3, spacing_km=10)


def series(wet_hours=(12, 13, 14), first=6, last=21, **kw):
    """One list per route point: hourly from ``first`` to ``last`` UTC, wet at ``wet_hours``."""
    day = T0.replace(hour=0)
    hours = [
        HourlyWeather(
            time=day + timedelta(hours=h),
            precipitation_probability=80 if h in wet_hours else 0,
            wind_speed_kmh=10,
            wind_from_deg=0,
            temperature_c=10,
            is_day=True,
            **kw,
        )
        for h in range(first, last + 1)
    ]
    return [list(hours) for _ in POINTS]


def options(departure=T0, offsets=range(-3, 7), duration_s=3600, **kw):
    return departure_options(
        POINTS,
        kw.pop("series", series()),
        departure=departure,
        duration_s=duration_s,
        offsets_h=list(offsets),
        **kw,
    )


def test_options_are_listed_in_time_order_with_the_requested_one_included():
    listed = options()
    assert [o.offset_minutes for o in listed] == [h * 60 for h in range(-3, 7)]
    requested = next(o for o in listed if o.offset_minutes == 0)
    assert requested.departure == T0 and requested.arrival == T0 + timedelta(hours=1)
    assert requested.samples == 3 and requested.wet_samples == 3
    assert options(offsets=[])[0].offset_minutes == 0  # the requested one is always there


def test_wet_samples_follow_the_hour_each_point_is_reached():
    by_offset = {o.offset_minutes // 60: o for o in options()}
    assert by_offset[3].wet_samples == 0 and by_offset[-3].wet_samples == 0
    assert by_offset[-1].wet_samples == 1  # only the last point is reached at 12:00
    assert by_offset[0].precipitation_probability_max == 80
    assert by_offset[3].precipitation_probability_max == 0


def test_the_suggestion_is_the_nearest_departure_that_avoids_the_rain():
    suggested = suggest_departure(options())
    assert suggested is not None and suggested.offset_minutes == -120  # 2 h earlier beats 3 h later


def test_nothing_is_suggested_when_the_requested_time_is_as_good_as_any():
    dry = series(wet_hours=())
    assert suggest_departure(options(series=dry)) is None
    # and not when the alternatives are not better: wet all day
    wet = series(wet_hours=range(0, 24))
    assert suggest_departure(options(series=wet)) is None


def test_alternatives_in_the_past_are_not_offered():
    listed = options(earliest=T0 - timedelta(minutes=30))
    assert min(o.offset_minutes for o in listed) == 0
    suggested = suggest_departure(listed)
    assert suggested is not None and suggested.offset_minutes == 180  # earlier ones are gone


def test_options_the_forecast_does_not_fully_cover_are_dropped_not_compared_partially():
    short = series(last=14)  # forecast ends at 14:00 UTC
    listed = options(series=short)
    assert max(o.offset_minutes for o in listed) <= 2 * 60  # 15:00+ would be outside coverage
    assert all(o.samples == 3 for o in listed)


def test_daylight_decides_when_the_rain_does_not():
    # Sunset is about 16:46 UTC here. A one-hour ride starting at 17:00 ends in the dark;
    # starting by 15:30 it ends in daylight.
    dry = series(wet_hours=())
    listed = options(departure=T0.replace(hour=17), series=dry, offsets=range(-4, 3))
    requested = next(o for o in listed if o.offset_minutes == 0)
    assert requested.after_dark is True
    suggested = suggest_departure(listed)
    assert suggested is not None and suggested.after_dark is False
    assert suggested.offset_minutes == -120  # nearest departure that finishes before sunset


def test_less_rain_is_never_bought_with_darkness():
    # Rain until 17:59 UTC; sunset is about 16:46. Requested 15:00 (light, wet): leaving at
    # 19:00 is drier but in the dark, so it must not be suggested; nothing else is better.
    wet = series(wet_hours=range(12, 18))
    listed = options(departure=T0.replace(hour=15), series=wet, offsets=[-3, 1, 4])
    assert any(o.after_dark and o.wet_samples == 0 for o in listed)  # tempting, but dark
    assert suggest_departure(listed) is None


def route_weather(departure=T0, wet_hours=(12, 13, 14), offsets=range(-3, 7), **kw):
    return build_route_weather(
        POINTS,
        series(wet_hours=wet_hours),
        departure=departure,
        duration_s=3600,
        duration_source="provider",
        provider="p",
        attribution="a",
        retrieved_at=T0,
        distance_m=55_000,
        option_offsets_h=list(offsets),
        **kw,
    )


def test_the_route_weather_carries_the_options_and_a_plain_advisory():
    weather = route_weather()
    assert len(weather.departure_options) == 10
    assert weather.suggested_departure == T0 - timedelta(hours=2)
    assert "Leaving 2 hours earlier would avoid the forecast precipitation on this route." in (
        weather.advisories
    )


def test_a_later_suggestion_is_worded_as_later_and_one_hour_is_singular():
    weather = route_weather(wet_hours=(12,), earliest=T0, offsets=range(-3, 4))
    assert weather.suggested_departure == T0 + timedelta(hours=1)
    assert "Leaving 1 hour later would avoid the forecast precipitation on this route." in (
        weather.advisories
    )


def test_a_partial_improvement_is_worded_as_less_precipitation_not_none():
    # Rain 12:00-14:59; leaving at 14:00 meets only part of it (the last point is past it).
    weather = route_weather(earliest=T0, offsets=range(-3, 3))
    assert weather.suggested_departure == T0 + timedelta(hours=2)
    assert "Leaving 2 hours later would have less precipitation along this route." in (
        weather.advisories
    )


def test_a_dry_requested_time_gets_no_suggestion_note():
    weather = route_weather(wet_hours=())
    assert weather.suggested_departure is None
    assert not any(n.startswith("Leaving") for n in weather.advisories)
    assert len(weather.departure_options) == 10  # the comparison is still listed


def test_switched_off_means_no_options_at_all():
    weather = route_weather(offsets=[])
    assert weather.departure_options == [] and weather.suggested_departure is None


def test_a_single_option_is_not_a_comparison():
    assert route_weather(offsets=[0]).departure_options == []
