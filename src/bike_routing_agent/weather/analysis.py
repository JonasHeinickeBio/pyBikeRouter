"""From a route and an hourly forecast to "what will it be like when I ride it".

All pure functions. The forecast is sampled where and *when* the rider is
expected to be (position along the route -> departure + duration * fraction),
the wind is resolved into head/tail and cross components against the local
direction of travel, and a summary plus plain-fact advisories are derived.

Nothing here scores or ranks routes -- weights for weather would need the same
calibration evidence as every other score term -- and the advisories state
forecast facts ("gusts up to 55 km/h"), never judgements about safety.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from bike_routing_agent.weather.daylight import local_solar_date, sun_times
from bike_routing_agent.weather.models import (
    CONDITION_SEVERITY,
    Condition,
    Daylight,
    HourlyWeather,
    RouteWeather,
    WeatherSample,
    WeatherSummary,
    WetStretch,
)

EARTH_RADIUS_M = 6_371_008.8
# Assumed when the routing engine reported no duration. The forecast hours
# depend on it, so the result says so (``duration_source="estimated"``).
ESTIMATED_SPEED_KMH = 15.0
# A sample further than this from any forecast hour is "not covered", not guessed.
MAX_HOUR_GAP = timedelta(minutes=90)
# Head/tailwind of at least this counts toward the head/tailwind shares.
WIND_SHARE_THRESHOLD_KMH = 10.0

# Advisory thresholds: when a forecast fact is worth stating. They are about
# *what is mentioned*, not about safe/unsafe, and are not calibrated.
RAIN_PROBABILITY_NOTE = 60.0
RAIN_MM_PER_HOUR_NOTE = 1.0
GUST_NOTE_KMH = 50.0
WIND_NOTE_KMH = 30.0
HEADWIND_NOTE_KMH = 15.0
COLD_NOTE_C = 3.0
HEAT_NOTE_C = 32.0
UV_NOTE = 6.0
# A sample counts as wet for the "where is the rain" stretch from this on.
WET_PROBABILITY = RAIN_PROBABILITY_NOTE
WET_MM_PER_HOUR = 0.3
WET_CONDITIONS: frozenset[str] = frozenset(
    {"drizzle", "rain", "freezing_rain", "snow", "thunderstorm"}
)
# Feels-like is worth stating when it is at least this much colder/warmer than the air.
FEELS_LIKE_GAP_C = 3.0
# Mention the light running out when sunset follows arrival within this long.
SUNSET_SOON_MIN = 45.0


@dataclass(frozen=True)
class RoutePoint:
    fraction: float
    lon: float
    lat: float
    bearing_deg: float


def line_coordinates(geometry: dict[str, Any]) -> list[tuple[float, float]]:
    """``(lon, lat)`` vertices of a (Multi)LineString; empty for anything else."""
    kind = geometry.get("type")
    raw = geometry.get("coordinates") or []
    if kind == "LineString":
        parts = [raw]
    elif kind == "MultiLineString":
        parts = raw
    else:
        return []
    return [(float(p[0]), float(p[1])) for part in parts for p in part if len(p) >= 2]


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def initial_bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Compass bearing (0 = north, clockwise) of the great-circle path a -> b."""
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _cumulative(coords: Sequence[tuple[float, float]]) -> list[float]:
    total = [0.0]
    for a, b in zip(coords, coords[1:], strict=False):
        total.append(total[-1] + haversine_m(a, b))
    return total


def _point_at(
    coords: Sequence[tuple[float, float]], cumulative: Sequence[float], distance_m: float
) -> tuple[float, float]:
    distance_m = min(max(distance_m, 0.0), cumulative[-1])
    j = 0
    while j < len(cumulative) - 2 and cumulative[j + 1] < distance_m:
        j += 1
    span = cumulative[j + 1] - cumulative[j]
    t = 0.0 if span == 0 else (distance_m - cumulative[j]) / span
    (x0, y0), (x1, y1) = coords[j], coords[j + 1]
    return (x0 + t * (x1 - x0), y0 + t * (y1 - y0))


def sample_route(
    geometry: dict[str, Any], *, max_samples: int = 5, spacing_km: float = 10.0
) -> list[RoutePoint]:
    """Points spread evenly along the route, each with its local direction of travel.

    About one point per ``spacing_km`` (at least start and end, at most
    ``max_samples``): forecast grids are several kilometres wide, so denser
    sampling would only buy duplicate answers. Unusable geometry gives ``[]``.
    """
    coords = line_coordinates(geometry)
    if len(coords) < 2:
        return []
    cumulative = _cumulative(coords)
    length = cumulative[-1]
    if length <= 0:
        return []
    count = min(max(math.ceil(length / 1000.0 / spacing_km) + 1, 2), max(2, max_samples))
    delta = min(length * 0.05, 500.0)
    points: list[RoutePoint] = []
    for i in range(count):
        fraction = i / (count - 1)
        here = fraction * length
        before = _point_at(coords, cumulative, here - delta)
        after = _point_at(coords, cumulative, here + delta)
        lon, lat = _point_at(coords, cumulative, here)
        points.append(RoutePoint(fraction, lon, lat, initial_bearing(before, after)))
    return points


def wind_components(
    speed_kmh: float, wind_from_deg: float, bearing_deg: float
) -> tuple[float, float]:
    """``(headwind, crosswind)`` in km/h against the direction of travel.

    ``wind_from_deg`` is where the wind comes *from*. A rider heading north
    (0 deg) into a north wind (0 deg) has the full speed as headwind; the same
    wind from the south (180 deg) is a tailwind (negative). A positive
    crosswind blows from the rider's right.
    """
    relative = math.radians(wind_from_deg - bearing_deg)
    return speed_kmh * math.cos(relative), speed_kmh * math.sin(relative)


def pick_hour(series: Sequence[HourlyWeather], when: datetime) -> HourlyWeather | None:
    """The forecast hour nearest to ``when``, or ``None`` if none is close enough."""
    best: HourlyWeather | None = None
    best_gap: timedelta | None = None
    for entry in series:
        gap = abs(entry.time - when)
        if best_gap is None or gap < best_gap:
            best, best_gap = entry, gap
    return best if best is not None and best_gap is not None and best_gap <= MAX_HOUR_GAP else None


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _present(values: Sequence[float | None]) -> list[float]:
    return [v for v in values if v is not None]


def _worst(conditions: Sequence[Condition]) -> Condition:
    return max(conditions, key=CONDITION_SEVERITY.index, default="unknown")


def _is_wet(hour: HourlyWeather) -> bool:
    return (
        (
            hour.precipitation_probability is not None
            and hour.precipitation_probability >= WET_PROBABILITY
        )
        or (hour.precipitation_mm is not None and hour.precipitation_mm >= WET_MM_PER_HOUR)
        or hour.condition in WET_CONDITIONS
    )


def wet_stretch(samples: Sequence[WeatherSample], distance_m: float | None) -> WetStretch | None:
    """From the first to the last wet sample along the route, or ``None`` when dry."""
    wet = [s for s in samples if _is_wet(s.weather)]
    if not wet:
        return None
    first, last = wet[0], wet[-1]
    km = (lambda f: f * distance_m / 1000.0) if distance_m else (lambda f: None)
    return WetStretch(
        from_fraction=first.fraction,
        to_fraction=last.fraction,
        from_km=km(first.fraction),
        to_km=km(last.fraction),
        from_time=first.time,
        to_time=last.time,
        whole_route=len(wet) == len(samples),
    )


def daylight_for(
    points: Sequence[RoutePoint], departure: datetime, arrival: datetime
) -> Daylight | None:
    """Sunrise/sunset at the start of the route and how they sit around the ride."""
    if not points:
        return None
    start = points[0]
    times = sun_times(local_solar_date(departure, start.lon), start.lat, start.lon)
    if times is None:
        return None
    sunrise, sunset = times
    return Daylight(
        sunrise=sunrise,
        sunset=sunset,
        minutes_of_light_left_at_arrival=(sunset - arrival).total_seconds() / 60.0,
        minutes_before_sunrise_at_start=(sunrise - departure).total_seconds() / 60.0,
    )


def summarize(
    samples: Sequence[WeatherSample], *, distance_m: float | None = None
) -> WeatherSummary:
    hours = [s.weather for s in samples]
    temps = _present([h.temperature_c for h in hours])
    feels = _present([h.apparent_temperature_c for h in hours])
    speeds = _present([h.wind_speed_kmh for h in hours])
    headwinds = _present([s.headwind_kmh for s in samples])
    day_flags = [h.is_day for h in hours if h.is_day is not None]
    return WeatherSummary(
        temperature_min_c=min(temps, default=None),
        temperature_max_c=max(temps, default=None),
        apparent_temperature_min_c=min(feels, default=None),
        apparent_temperature_max_c=max(feels, default=None),
        wet_stretch=wet_stretch(samples, distance_m),
        precipitation_probability_max=max(
            _present([h.precipitation_probability for h in hours]), default=None
        ),
        precipitation_mm_per_hour_max=max(
            _present([h.precipitation_mm for h in hours]), default=None
        ),
        wind_speed_mean_kmh=_mean(speeds),
        wind_speed_max_kmh=max(speeds, default=None),
        wind_gust_max_kmh=max(_present([h.wind_gust_kmh for h in hours]), default=None),
        headwind_mean_kmh=_mean(headwinds),
        headwind_share=(
            sum(1 for v in headwinds if v >= WIND_SHARE_THRESHOLD_KMH) / len(headwinds)
            if headwinds
            else None
        ),
        tailwind_share=(
            sum(1 for v in headwinds if v <= -WIND_SHARE_THRESHOLD_KMH) / len(headwinds)
            if headwinds
            else None
        ),
        uv_index_max=max(_present([h.uv_index for h in hours]), default=None),
        worst_condition=_worst([h.condition for h in hours]),
        after_dark=(not all(day_flags)) if day_flags else None,
    )


def _km(value: float) -> str:
    return f"{value:.0f}"


def _wet_note(stretch: WetStretch) -> str:
    if stretch.whole_route:
        return "Precipitation is forecast along the whole route."
    if stretch.from_km is None or stretch.to_km is None:
        return "Precipitation is forecast along part of the route."
    start, end = _km(stretch.from_km), _km(stretch.to_km)
    if start == end:
        return f"Precipitation is forecast around km {start} of the route."
    return f"Precipitation is forecast between about km {start} and km {end} of the route."


def _daylight_note(daylight: Daylight) -> str | None:
    if daylight.minutes_before_sunrise_at_start > 0:
        return (
            f"The ride starts about {daylight.minutes_before_sunrise_at_start:.0f} "
            "minutes before sunrise."
        )
    left = daylight.minutes_of_light_left_at_arrival
    if left < 0:
        return f"You arrive about {-left:.0f} minutes after sunset."
    if left <= SUNSET_SOON_MIN:
        return f"Sunset is about {left:.0f} minutes after you arrive."
    return None


def advisories(summary: WeatherSummary, daylight: Daylight | None = None) -> list[str]:
    """Forecast facts worth stating. Never a statement about safety."""
    notes: list[str] = []
    condition = summary.worst_condition
    if condition == "thunderstorm":
        notes.append("Thunderstorms are in the forecast along the route.")
    elif condition in ("snow", "freezing_rain"):
        notes.append("Snow or freezing rain is in the forecast along the route.")
    elif condition == "fog":
        notes.append("Fog is in the forecast along the route.")

    probability = summary.precipitation_probability_max
    rate = summary.precipitation_mm_per_hour_max
    if condition not in ("thunderstorm", "snow", "freezing_rain") and (
        (probability is not None and probability >= RAIN_PROBABILITY_NOTE)
        or (rate is not None and rate >= RAIN_MM_PER_HOUR_NOTE)
    ):
        detail = []
        if probability is not None:
            detail.append(f"up to {probability:.0f}% chance of precipitation")
        if rate is not None and rate > 0:
            detail.append(f"up to {rate:.1f} mm/h")
        notes.append("Rain is likely: " + ", ".join(detail) + ".")

    if summary.wet_stretch is not None:
        notes.append(_wet_note(summary.wet_stretch))

    gust = summary.wind_gust_max_kmh
    if gust is not None and gust >= GUST_NOTE_KMH:
        notes.append(f"Wind gusts up to {gust:.0f} km/h are forecast.")
    wind = summary.wind_speed_max_kmh
    if wind is not None and wind >= WIND_NOTE_KMH:
        notes.append(f"Sustained wind up to {wind:.0f} km/h is forecast.")

    headwind = summary.headwind_mean_kmh
    if headwind is not None and headwind >= HEADWIND_NOTE_KMH:
        notes.append(f"On average {headwind:.0f} km/h of headwind along the route.")
    elif headwind is not None and headwind <= -HEADWIND_NOTE_KMH:
        notes.append(f"On average {-headwind:.0f} km/h of tailwind along the route.")

    cold = summary.temperature_min_c
    if cold is not None and cold <= 0:
        notes.append(f"Temperatures at or below freezing are forecast ({cold:.0f} °C).")
    elif cold is not None and cold <= COLD_NOTE_C:
        notes.append(f"Temperatures down to {cold:.0f} °C are forecast.")
    heat = summary.temperature_max_c
    if heat is not None and heat >= HEAT_NOTE_C:
        notes.append(f"Temperatures up to {heat:.0f} °C are forecast.")
    feels = summary.apparent_temperature_min_c
    if (
        feels is not None
        and cold is not None
        and cold - feels >= FEELS_LIKE_GAP_C
        and feels <= COLD_NOTE_C + FEELS_LIKE_GAP_C
    ):
        notes.append(f"With the wind it feels like {feels:.0f} °C.")
    uv = summary.uv_index_max
    if uv is not None and uv >= UV_NOTE:
        notes.append(f"UV index up to {uv:.0f} is forecast.")
    # Sunrise and sunset are computed for the place and date, so they are preferred
    # to the forecast's daylight flag; the flag is the fallback (e.g. polar days).
    daylight_note = _daylight_note(daylight) if daylight is not None else None
    if daylight_note:
        notes.append(daylight_note)
    elif daylight is None and summary.after_dark:
        notes.append("Part of the ride is after dark according to the forecast.")
    return notes


def build_route_weather(
    points: Sequence[RoutePoint],
    series_per_point: Sequence[Sequence[HourlyWeather]],
    *,
    departure: datetime,
    duration_s: float,
    duration_source: str,
    provider: str,
    attribution: str,
    retrieved_at: datetime,
    distance_m: float | None = None,
) -> RouteWeather | None:
    """Weather for one route, or ``None`` when the forecast covers none of it."""
    samples: list[WeatherSample] = []
    for point, series in zip(points, series_per_point, strict=True):
        when = departure + timedelta(seconds=duration_s * point.fraction)
        hour = pick_hour(series, when)
        if hour is None:
            continue
        headwind = crosswind = None
        if hour.wind_speed_kmh is not None and hour.wind_from_deg is not None:
            headwind, crosswind = wind_components(
                hour.wind_speed_kmh, hour.wind_from_deg, point.bearing_deg
            )
        samples.append(
            WeatherSample(
                fraction=point.fraction,
                lon=point.lon,
                lat=point.lat,
                time=when,
                bearing_deg=point.bearing_deg,
                weather=hour,
                headwind_kmh=headwind,
                crosswind_kmh=crosswind,
            )
        )
    if not samples:
        return None
    arrival = departure + timedelta(seconds=duration_s)
    summary = summarize(samples, distance_m=distance_m)
    daylight = daylight_for(points, departure, arrival)
    return RouteWeather(
        provider=provider,
        attribution=attribution,
        departure=departure,
        arrival=arrival,
        duration_source="provider" if duration_source == "provider" else "estimated",
        retrieved_at=retrieved_at,
        samples=samples,
        summary=summary,
        daylight=daylight,
        advisories=advisories(summary, daylight),
    )
