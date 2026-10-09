"""BRouter's map data comes in 5x5 degree segment files (``E10_N50.rd5``).

An engine that has no file for part of a trip answers ``datafile W5_N50.rd5 not found``;
these helpers name the files a trip touches and where the official ones are published, so a
client can tell the operator what is missing.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

SEGMENTS_BASE_URL = "https://brouter.de/brouter/segments4/"
_MISSING = re.compile(r"datafile\s+([EW]\d{1,3}_[NS]\d{1,2})\.rd5\s+not found", re.IGNORECASE)


def segment_name(lon: float, lat: float) -> str:
    """The segment file (without ``.rd5``) that covers a point, e.g. ``W5_N50``."""
    west_edge = math.floor(lon / 5) * 5
    south_edge = math.floor(lat / 5) * 5
    return (
        f"{'E' if west_edge >= 0 else 'W'}{abs(west_edge)}"
        f"_{'N' if south_edge >= 0 else 'S'}{abs(south_edge)}"
    )


def segments_for_points(points: Iterable[tuple[float, float]]) -> list[str]:
    """Distinct segment names for ``(lon, lat)`` points, in first-seen order."""
    seen: dict[str, None] = {}
    for lon, lat in points:
        seen.setdefault(segment_name(lon, lat), None)
    return list(seen)


def missing_segment(body: str) -> str | None:
    """The segment BRouter says it lacks (``"W5_N50"``), or ``None`` for any other message."""
    match = _MISSING.search(body)
    return match.group(1).upper() if match else None
