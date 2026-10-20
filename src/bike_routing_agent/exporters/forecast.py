"""The forecast along a route, as it goes into the GPX and GeoJSON files.

A rider's device shows waypoints, so each forecast point becomes a named GPX waypoint with the
time the rider is expected there and a one-line description ("8 °C, rain 2 %, wind 19 km/h from E,
headwind 12 km/h"); GeoJSON gets the same facts as data. The data credit (Open-Meteo, DWD ... are
CC BY) travels with the file. Everything here is built from the candidate's own ``weather`` and is
mirrored in ``frontend/export.js`` (a test compares both): the wording and the rounding are the
same on purpose.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from bike_routing_agent.models import RouteCandidate

COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
# Head/tailwind below this is "across the route" and not worth a word.
WIND_NOTE_KMH = 3.0


def round_half_up(value: float) -> int:
    """JavaScript's ``Math.round`` (Python's ``round`` is half-to-even), so both sides agree."""
    return int(math.floor(value + 0.5))


def compass(degrees: float) -> str:
    return COMPASS[round_half_up((degrees % 360) / 45) % 8]


def utc(value: str | datetime) -> datetime:
    parsed = (
        datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    )
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def gpx_time(value: str | datetime) -> str:
    return utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def clock(value: str | datetime) -> str:
    return utc(value).strftime("%H:%M")


def sample_name(sample: dict[str, Any]) -> str:
    return f"Forecast {clock(sample['time'])} UTC"


def sample_description(sample: dict[str, Any]) -> str:
    """What the forecast says at one point, in one line."""
    hour = sample.get("weather") or {}
    parts: list[str] = []
    if hour.get("temperature_c") is not None:
        parts.append(f"{round_half_up(hour['temperature_c'])} °C")
    if hour.get("precipitation_probability") is not None:
        parts.append(f"rain {round_half_up(hour['precipitation_probability'])} %")
    if hour.get("wind_speed_kmh") is not None and hour.get("wind_from_deg") is not None:
        speed = round_half_up(hour["wind_speed_kmh"])
        parts.append(f"wind {speed} km/h from {compass(hour['wind_from_deg'])}")
    head = sample.get("headwind_kmh")
    if head is not None and abs(head) >= WIND_NOTE_KMH:
        word = "headwind" if head > 0 else "tailwind"
        parts.append(f"{word} {round_half_up(abs(head))} km/h")
    return ", ".join(parts)


def wind_effect_text(effect: dict[str, Any] | None) -> str | None:
    """ "+12 min" / "-5 min" (a plain minus: this goes into a file), or ``None``."""
    if not effect or effect.get("effect_s") is None:
        return None
    minutes = round_half_up(abs(effect["effect_s"]) / 60)
    if minutes == 0:
        return None
    return f"{'-' if effect['effect_s'] < 0 else '+'}{minutes} min"


def description(weather: dict[str, Any]) -> str:
    """The sentence in the GPX metadata: when the forecast is for, the wind's effect on the time (an
    estimate) and the data credit."""
    left = utc(weather["departure"]).strftime("%Y-%m-%d %H:%M")
    text = f"Forecast for a ride leaving {left} UTC."
    effect = wind_effect_text(weather.get("wind_effect"))
    if effect:
        text += f" Estimated effect of the wind on the time: {effect}."
    credit = weather.get("attribution")
    return f"{text} {credit}" if credit else text


def weather_of(candidate: RouteCandidate) -> dict[str, Any] | None:
    """The candidate's forecast as JSON-shaped data, or ``None`` without one."""
    if candidate.weather is None:
        return None
    data = candidate.model_dump(mode="json")["weather"]
    return data if data and data.get("samples") else None


def geojson_forecast(weather: dict[str, Any]) -> dict[str, Any]:
    """The forecast block of the GeoJSON properties."""
    summary = weather.get("summary") or {}
    keys = (
        "temperature_min_c",
        "temperature_max_c",
        "precipitation_probability_max",
        "wind_speed_max_kmh",
        "wind_gust_max_kmh",
        "headwind_mean_kmh",
    )
    return {
        "departure": weather["departure"],
        "arrival": weather["arrival"],
        "attribution": weather.get("attribution"),
        "summary": {key: summary.get(key) for key in keys},
        "wind_effect": weather.get("wind_effect"),
        "advisories": weather.get("advisories") or [],
        "points": [
            {
                "fraction": s["fraction"],
                "lon": s["lon"],
                "lat": s["lat"],
                "time": s["time"],
                "temperature_c": (s.get("weather") or {}).get("temperature_c"),
                "precipitation_probability": (s.get("weather") or {}).get(
                    "precipitation_probability"
                ),
                "wind_speed_kmh": (s.get("weather") or {}).get("wind_speed_kmh"),
                "wind_from_deg": (s.get("weather") or {}).get("wind_from_deg"),
                "headwind_kmh": s.get("headwind_kmh"),
                "condition": (s.get("weather") or {}).get("condition"),
            }
            for s in weather["samples"]
        ],
    }
