"""Mapping provider weather codes onto one small, provider-neutral vocabulary."""

from __future__ import annotations

import re

from bike_routing_agent.weather.models import Condition

CONDITION_LABELS: dict[Condition, str] = {
    "clear": "Clear",
    "partly_cloudy": "Partly cloudy",
    "cloudy": "Cloudy",
    "fog": "Fog",
    "drizzle": "Drizzle",
    "rain": "Rain",
    "freezing_rain": "Freezing rain",
    "snow": "Snow",
    "thunderstorm": "Thunderstorms",
    "unknown": "Unknown",
}


def condition_from_wmo(code: int | None) -> Condition:
    """WMO weather interpretation codes, as used by Open-Meteo (``weather_code``)."""
    if code is None:
        return "unknown"
    if code == 0:
        return "clear"
    if code in (1, 2):
        return "partly_cloudy"
    if code == 3:
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if code in (51, 53, 55):
        return "drizzle"
    if code in (56, 57, 66, 67):
        return "freezing_rain"
    if code in (61, 63, 65, 80, 81, 82):
        return "rain"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (95, 96, 99):
        return "thunderstorm"
    return "unknown"


_DAYTIME_SUFFIX = re.compile(r"_(day|night|polartwilight)$")


def condition_from_met_symbol(symbol: str | None) -> Condition:
    """MET Norway ``symbol_code`` (e.g. ``lightrainshowers_day``, ``heavysnow``)."""
    if not symbol:
        return "unknown"
    name = _DAYTIME_SUFFIX.sub("", symbol)
    if "thunder" in name:
        return "thunderstorm"
    if "sleet" in name:
        return "freezing_rain"
    if "snow" in name:
        return "snow"
    if "rain" in name:
        return "rain"
    if name == "fog":
        return "fog"
    if name in ("clearsky", "fair"):
        return "clear" if name == "clearsky" else "partly_cloudy"
    if name == "partlycloudy":
        return "partly_cloudy"
    if name == "cloudy":
        return "cloudy"
    return "unknown"
