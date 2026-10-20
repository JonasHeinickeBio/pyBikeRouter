"""The wind's effect on the travel time: the power model, and how it enters the weather."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bike_routing_agent.nodes.weather import build_weather_node
from bike_routing_agent.weather.analysis import build_route_weather, sample_route
from bike_routing_agent.weather.models import HourlyWeather
from bike_routing_agent.weather.service import WeatherService
from bike_routing_agent.weather.wind_time import (
    MAX_SPEED_M_S,
    MIN_SPEED_M_S,
    RIDERS,
    Rider,
    WindTime,
    describe,
    rider_for,
    speed_in_wind,
    wind_time,
)
from bike_routing_agent.weather.wind_time import _power_w as power_w

ROAD = RIDERS["road"]
KMH = 1 / 3.6
V20 = 20 * KMH  # a 20 km/h pace


def ride(headwinds, *, km=50.0, pace_kmh=20.0, ascent_m=0.0, rider=ROAD):
    """The effect of constant or varying headwinds ((fraction, km/h) pairs) on one ride."""
    distance = km * 1000
    return wind_time(
        distance_m=distance,
        duration_s=distance / (pace_kmh * KMH),
        ascent_m=ascent_m,
        points=headwinds,
        rider=rider,
    )


# --------------------------------------------------------------------------- the physics


def test_the_power_model_matches_the_textbook_for_a_road_bike_at_40_kmh():
    force = ROAD.mass_kg * 9.81 * ROAD.crr
    watts = power_w(40 * KMH, 0.0, force, ROAD.cda)
    assert 250 <= watts <= 360  # a 40 km/h solo ride on the flat takes roughly 300 W


def test_no_wind_means_no_change():
    assert speed_in_wind(V20, 0.0, ROAD) == V20
    effect = ride([(0.0, 0.0), (1.0, 0.0)])
    assert effect is not None and effect.effect_s == pytest.approx(0, abs=1e-6)
    assert effect.adjusted_s == pytest.approx(effect.still_air_s)


def test_a_headwind_slows_and_a_tailwind_speeds_up_and_more_wind_means_more_effect():
    speeds = [speed_in_wind(V20, w * KMH, ROAD) for w in (-20, -10, 0, 10, 20)]
    assert speeds == sorted(speeds, reverse=True)  # strictly slower the more it blows against you
    assert speeds[0] > V20 > speeds[-1]


def test_the_same_power_needs_a_known_speed_in_a_headwind():
    # 20 km/h in still air takes ~56 W; with a 10 km/h headwind the same power gives ~15 km/h.
    v = speed_in_wind(V20, 10 * KMH, ROAD)
    assert v / KMH == pytest.approx(14.8, abs=0.5)
    force = ROAD.mass_kg * 9.81 * ROAD.crr
    assert power_w(v, 10 * KMH, force, ROAD.cda) == pytest.approx(
        power_w(V20, 0.0, force, ROAD.cda), rel=1e-3
    )  # the power really is the same


def test_a_headwind_costs_more_time_than_the_same_tailwind_saves():
    slower = ride([(0.0, 15.0), (1.0, 15.0)]).effect_s
    faster = -ride([(0.0, -15.0), (1.0, -15.0)]).effect_s
    assert slower > faster > 0


def test_a_larger_frontal_area_feels_the_wind_more_and_the_range_reflects_it():
    upright = Rider(ROAD.mass_kg, ROAD.crr, 0.58)
    assert (
        ride([(0, 15.0), (1, 15.0)], rider=upright).effect_s > ride([(0, 15.0), (1, 15.0)]).effect_s
    )
    head = ride([(0.0, 15.0), (1.0, 15.0)])
    assert head.low_s < head.effect_s < head.high_s  # a headwind: more area, more loss
    tail = ride([(0.0, -15.0), (1.0, -15.0)])
    assert (
        tail.low_s <= tail.effect_s <= tail.high_s
    )  # a tailwind: more area, more gain (more negative)
    assert tail.low_s != tail.high_s


def test_climbing_takes_power_so_the_wind_matters_less_on_a_hilly_ride():
    flat = ride([(0.0, 15.0), (1.0, 15.0)], ascent_m=0.0)
    hilly = ride([(0.0, 15.0), (1.0, 15.0)], ascent_m=1200.0)
    assert 0 < hilly.effect_s / hilly.still_air_s < flat.effect_s / flat.still_air_s


def test_the_speed_stays_within_what_a_cyclist_does():
    assert speed_in_wind(V20, 60 * KMH, ROAD) >= MIN_SPEED_M_S  # a gale: slow, but not stopped
    assert speed_in_wind(V20, -80 * KMH, ROAD) <= MAX_SPEED_M_S  # a hurricane in the back


def test_a_heavy_bike_in_a_strong_tailwind_is_not_read_as_slower_than_the_wind():
    heavy = Rider(150.0, 0.03, 0.9)
    v = speed_in_wind(3.0, -15.0, heavy)  # 3 m/s pace, 15 m/s tailwind
    assert 3.0 <= v <= MAX_SPEED_M_S


# ---------------------------------------------------------------------- routes and stretches


def test_a_single_forecast_point_covers_the_whole_route_like_two_equal_ones():
    one = ride([(0.5, 12.0)])
    two = ride([(0.0, 12.0), (1.0, 12.0)])
    assert one.effect_s == pytest.approx(two.effect_s)


def test_each_stretch_takes_the_mean_of_the_two_points_around_it():
    # head 15 then tail 15: the middle stretches average out, the ends keep their own wind
    effect = ride([(0.0, 15.0), (0.5, 15.0), (1.0, -15.0)])
    uniform_head = ride([(0.0, 15.0), (1.0, 15.0)])
    uniform_tail = ride([(0.0, -15.0), (1.0, -15.0)])
    assert uniform_tail.effect_s < effect.effect_s < uniform_head.effect_s


def test_a_point_without_a_headwind_is_skipped_and_none_at_all_means_no_estimate():
    assert ride([(0.0, None), (0.5, 12.0), (1.0, None)]) is not None
    assert ride([(0.0, None), (1.0, None)]) is None
    assert ride([]) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"distance_m": 0.0, "duration_s": 3600.0},
        {"distance_m": 20_000.0, "duration_s": 0.0},
        {"distance_m": 20_000.0, "duration_s": 20_000.0 / 0.5},  # a 0.5 m/s walk
        {"distance_m": 200_000.0, "duration_s": 3600.0},  # 55 m/s: not a bicycle
    ],
)
def test_a_ride_the_model_does_not_describe_gets_no_estimate(kwargs):
    assert wind_time(ascent_m=0.0, points=[(0.0, 10.0)], rider=ROAD, **kwargs) is None


def test_every_bike_type_but_the_ebike_has_a_rider():
    for bike in ("road", "gravel", "touring", "mountain", "city", "commuter", "recumbent"):
        rider = rider_for(bike)
        assert rider is not None and 70 <= rider.mass_kg <= 110 and 0.002 <= rider.crr <= 0.015
        assert 0.25 <= rider.cda <= 0.65
    assert rider_for("ebike") is None and rider_for("unicycle") is None


# ------------------------------------------------------------------------------ describing


def test_a_small_effect_is_not_stated_and_a_real_one_has_a_range_and_a_direction():
    tiny = WindTime(3600.0, 3700.0, 100.0, 80.0, 120.0)  # < 3 min
    assert describe(tiny) is None
    assert (
        describe(WindTime(36000.0, 36300.0, 300.0, 250.0, 350.0)) is None
    )  # 5 min is < 3 % of 10 h
    slower = describe(WindTime(7200.0, 7920.0, 720.0, 540.0, 900.0))
    assert slower is not None and "about 12 min longer (9-15 min)" in slower
    assert "estimate" in slower and "still-air" in slower
    faster = describe(WindTime(7200.0, 6600.0, -600.0, -720.0, -480.0))
    assert faster is not None and "about 10 min less (8-12 min)" in faster


# -------------------------------------------------------------------------- in the weather

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
NORTH = {"type": "LineString", "coordinates": [[10.0, 52.0], [10.0, 52.5]]}  # ~55 km north
POINTS = sample_route(NORTH, max_samples=3, spacing_km=10)


def forecast(wind_from: float, speed: float = 20.0):
    return [
        [
            HourlyWeather(
                time=T0.replace(hour=h),
                wind_speed_kmh=speed,
                wind_from_deg=wind_from,
                temperature_c=10,
                is_day=True,
            )
            for h in range(6, 22)
        ]
        for _ in POINTS
    ]


def weather(wind_from=0.0, *, bike="road", source="provider", duration_s=10_000.0, **kw):
    return build_route_weather(
        POINTS,
        forecast(wind_from),
        departure=T0,
        duration_s=duration_s,
        duration_source=source,
        provider="fake",
        attribution="x",
        retrieved_at=T0,
        distance_m=55_000.0,
        bike_type=bike,
        ascent_m=100.0,
        **kw,
    )


def test_a_headwind_gives_a_longer_time_a_tailwind_a_shorter_one_and_it_is_stated():
    head = weather(0.0)  # riding north into a north wind
    assert head.wind_effect is not None and head.wind_effect.effect_s > 0
    assert head.wind_effect.adjusted_s == pytest.approx(10_000 + head.wind_effect.effect_s)
    low, high = head.wind_effect.range_s
    assert low < head.wind_effect.effect_s < high
    assert any("longer" in note and "still-air" in note for note in head.advisories)
    tail = weather(180.0)
    assert tail.wind_effect is not None and tail.wind_effect.effect_s < 0
    assert any("less" in note and "still-air" in note for note in tail.advisories)


def test_a_crosswind_does_not_change_the_time():
    across = weather(270.0)
    assert across.wind_effect is not None and abs(across.wind_effect.effect_s) < 30
    assert not any("still-air" in note for note in across.advisories)


def test_no_estimate_for_an_estimated_duration_an_ebike_or_no_bike_type():
    assert (
        weather(0.0, source="estimated").wind_effect is None
    )  # the forecast hours are a guess too
    assert weather(0.0, bike="ebike").wind_effect is None
    assert weather(0.0, bike=None).wind_effect is None
    assert not any("still-air" in n for n in weather(0.0, bike="ebike").advisories)


def test_each_departure_option_carries_the_effect_at_its_own_time():
    result = weather(0.0, option_offsets_h=[-1, 0, 1])
    requested = next(o for o in result.departure_options if o.offset_minutes == 0)
    assert requested.wind_effect_s == pytest.approx(result.wind_effect.effect_s)
    assert all(o.wind_effect_s is not None for o in result.departure_options)
    no_bike = weather(0.0, bike=None, option_offsets_h=[-1, 0, 1])
    assert all(o.wind_effect_s is None for o in no_bike.departure_options)


# --------------------------------------------------------------------------------- the node

NOW = datetime(2026, 10, 7, 12, 30, tzinfo=UTC)


class WindProvider:
    name = "fake"
    attribution = "data by fake"

    async def forecast(self, points, start, end):
        base = start.replace(minute=0, second=0, microsecond=0)
        return [
            [
                HourlyWeather(
                    time=base + timedelta(hours=i),
                    wind_speed_kmh=20.0,
                    wind_from_deg=0.0,  # from the north: a headwind going north
                    temperature_c=11.0,
                )
                for i in range(12)
            ]
            for _ in points
        ]

    async def health(self):
        return {"status": "ok"}


def node_state(bike_type: str | None):
    candidate = {
        "provider": "brouter",
        "provider_profile": "p",
        "geometry_geojson": {"type": "LineString", "coordinates": [[10.0, 52.0], [10.0, 52.18]]},
        "metrics": {"distance_m": 20_000, "duration_s": 3600, "ascent_m": 150},
        "score": 0.9,
        "warnings": [],
    }
    constraints = {"bike_type": bike_type} if bike_type else {}
    return {"candidates": [candidate], "selected_candidate": candidate, "constraints": constraints}


async def test_the_node_estimates_the_wind_effect_for_the_requests_bike_type():
    run = build_weather_node(service=WeatherService([WindProvider()]), now=lambda: NOW)
    for bike in ("road", "touring", None):  # no bike type: the model default (gravel)
        update = await run(node_state(bike))
        assert update["candidates"][0]["weather"]["wind_effect"]["effect_s"] > 0, bike
    ebike = await run(node_state("ebike"))
    assert ebike["candidates"][0]["weather"]["wind_effect"] is None
