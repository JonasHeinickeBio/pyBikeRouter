"""Filling the gaps of one provider's forecast with another's.

Providers differ in what they publish: DWD has no UV index or feels-like, MET
Norway has no gusts or rain probability. Rather than choosing one and losing the
rest, the first provider that answers stays the **primary** (its values are never
replaced) and only the fields it leaves *missing* are taken from others, matched
by point and hour. Nothing is averaged: two forecast models disagreeing is not
resolved by splitting the difference, and a value always has one origin.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from bike_routing_agent.weather.models import HourlyWeather

# Optional fields worth filling; the core ones (temperature, wind speed and
# direction, precipitation amount, condition) always come from the primary.
FILLABLE_FIELDS: tuple[str, ...] = (
    "apparent_temperature_c",
    "uv_index",
    "wind_gust_kmh",
    "precipitation_probability",
)
# A secondary hour further than this from the primary hour is not the same hour.
MAX_HOUR_OFFSET = timedelta(minutes=30)


def missing_fields(series_per_point: Sequence[Sequence[HourlyWeather]]) -> set[str]:
    """Fillable fields that are missing in at least one hour of the forecast."""
    return {
        field
        for series in series_per_point
        for hour in series
        for field in FILLABLE_FIELDS
        if getattr(hour, field) is None
    }


def _nearest(series: Sequence[HourlyWeather], when: datetime) -> HourlyWeather | None:
    best = min(series, key=lambda h: abs(h.time - when), default=None)
    return best if best is not None and abs(best.time - when) <= MAX_HOUR_OFFSET else None


def fill_gaps(
    primary: Sequence[Sequence[HourlyWeather]],
    secondary: Sequence[Sequence[HourlyWeather]],
    fields: set[str],
) -> tuple[list[list[HourlyWeather]], set[str]]:
    """``primary`` with its missing ``fields`` taken from ``secondary``.

    Returns the merged series and the fields that were actually filled somewhere
    (empty when the secondary had nothing to add, so the caller can leave it out
    of the credits). A secondary of a different shape contributes nothing.
    """
    wanted = fields & set(FILLABLE_FIELDS)
    if len(primary) != len(secondary) or not wanted:
        return [list(s) for s in primary], set()
    filled: set[str] = set()
    merged: list[list[HourlyWeather]] = []
    for own, other in zip(primary, secondary, strict=True):
        hours: list[HourlyWeather] = []
        for hour in own:
            gaps = [f for f in wanted if getattr(hour, f) is None]
            donor = _nearest(other, hour.time) if gaps else None
            update = {f: getattr(donor, f) for f in gaps if donor and getattr(donor, f) is not None}
            if update:
                filled.update(update)
                hour = hour.model_copy(update=update)
            hours.append(hour)
        merged.append(hours)
    return merged, filled
