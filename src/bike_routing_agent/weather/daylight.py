"""Sunrise and sunset from the date and place alone (NOAA solar-position formulas).

Computed rather than fetched: it needs no provider, so it works with every
weather backend and offline, and it is accurate to a couple of minutes at
ordinary latitudes. Near the poles there can be no sunrise or sunset on a day
(midnight sun, polar night); the result is then ``None`` and callers say nothing
rather than guess.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

# Sun's centre 0.833 degrees below the horizon: refraction plus the solar radius.
_ZENITH_DEG = 90.833


def _solar_terms(day_of_year: int) -> tuple[float, float]:
    """``(equation of time in minutes, declination in radians)`` around local noon."""
    g = 2.0 * math.pi / 365.0 * (day_of_year - 1)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(g)
        - 0.032077 * math.sin(g)
        - 0.014615 * math.cos(2 * g)
        - 0.040849 * math.sin(2 * g)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(g)
        + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g)
        + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g)
        + 0.00148 * math.sin(3 * g)
    )
    return eqtime, decl


def local_solar_date(when: datetime, lon: float) -> date:
    """The calendar day at ``lon`` (by mean solar time) that contains ``when``."""
    return (when.astimezone(UTC) + timedelta(hours=lon / 15.0)).date()


def sun_times(day: date, lat: float, lon: float) -> tuple[datetime, datetime] | None:
    """``(sunrise, sunset)`` in UTC for the solar day ``day`` at the place, or ``None``
    when the sun does not rise or set that day."""
    eqtime, decl = _solar_terms(day.timetuple().tm_yday)
    phi = math.radians(lat)
    cos_ha = math.cos(math.radians(_ZENITH_DEG)) / (math.cos(phi) * math.cos(decl)) - math.tan(
        phi
    ) * math.tan(decl)
    if not -1.0 <= cos_ha <= 1.0:
        return None
    ha = math.degrees(math.acos(cos_ha))
    midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
    sunrise = midnight + timedelta(minutes=720.0 - 4.0 * (lon + ha) - eqtime)
    sunset = midnight + timedelta(minutes=720.0 - 4.0 * (lon - ha) - eqtime)
    return sunrise, sunset
