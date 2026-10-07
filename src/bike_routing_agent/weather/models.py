"""Provider-neutral weather models.

Kept free of imports from ``bike_routing_agent.models`` so that module can
embed :class:`RouteWeather` in a ``RouteCandidate`` without a cycle.
Every field a forecast may lack is optional: a missing value is reported as
missing (``None``), never as zero.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Condition = Literal[
    "clear",
    "partly_cloudy",
    "cloudy",
    "fog",
    "drizzle",
    "rain",
    "freezing_rain",
    "snow",
    "thunderstorm",
    "unknown",
]

# Least to most disruptive for a cyclist; the summary reports the worst one
# expected along the ride.
CONDITION_SEVERITY: tuple[Condition, ...] = (
    "unknown",
    "clear",
    "partly_cloudy",
    "cloudy",
    "fog",
    "drizzle",
    "rain",
    "snow",
    "freezing_rain",
    "thunderstorm",
)


class HourlyWeather(BaseModel):
    """One hour of forecast at one location (UTC)."""

    time: datetime
    temperature_c: float | None = None
    apparent_temperature_c: float | None = None
    precipitation_mm: float | None = Field(default=None, ge=0)
    precipitation_probability: float | None = Field(default=None, ge=0, le=100)
    wind_speed_kmh: float | None = Field(default=None, ge=0)
    wind_gust_kmh: float | None = Field(default=None, ge=0)
    # Meteorological convention: the direction the wind blows *from*, degrees
    # clockwise from north (a "west wind" is 270).
    wind_from_deg: float | None = None
    condition: Condition = "unknown"
    weather_code: int | None = None
    uv_index: float | None = Field(default=None, ge=0)
    is_day: bool | None = None


class WeatherSample(BaseModel):
    """The forecast where the rider is expected to be at a point of the route."""

    fraction: float = Field(ge=0, le=1)
    lon: float
    lat: float
    time: datetime
    bearing_deg: float | None = None
    weather: HourlyWeather
    # Positive = wind against the direction of travel, negative = tailwind.
    headwind_kmh: float | None = None
    crosswind_kmh: float | None = None


class WeatherSummary(BaseModel):
    temperature_min_c: float | None = None
    temperature_max_c: float | None = None
    precipitation_probability_max: float | None = None
    precipitation_mm_per_hour_max: float | None = None
    wind_speed_mean_kmh: float | None = None
    wind_speed_max_kmh: float | None = None
    wind_gust_max_kmh: float | None = None
    # Mean over the samples; positive = against the direction of travel.
    headwind_mean_kmh: float | None = None
    # Share of the route (by samples) with at least 10 km/h of head/tailwind.
    headwind_share: float | None = Field(default=None, ge=0, le=1)
    tailwind_share: float | None = Field(default=None, ge=0, le=1)
    uv_index_max: float | None = None
    worst_condition: Condition = "unknown"
    # True when the forecast says some part of the ride is after dark.
    after_dark: bool | None = None


class RouteWeather(BaseModel):
    provider: str
    attribution: str
    departure: datetime
    arrival: datetime
    # "provider" when the engine reported the duration, "estimated" when it was
    # derived from the distance (the forecast hours depend on it).
    duration_source: Literal["provider", "estimated"]
    retrieved_at: datetime
    samples: list[WeatherSample]
    summary: WeatherSummary
    # Facts about the forecast, never statements about safety.
    advisories: list[str] = Field(default_factory=list)
