"""What the OSM tags BRouter routed over say about a route.

BRouter's GeoJSON carries a ``messages`` table with the way tags of every
segment it chose, i.e. exactly the data its profile priced. The only thing
derived from it here is the share of the route on main roads without a bike
lane (``RouteMetrics.main_road_share``): a proxy for motor-traffic exposure,
stated as such wherever it is shown.
"""

from __future__ import annotations

from typing import Any

MAIN_ROADS = frozenset(
    {
        "trunk", "trunk_link", "primary", "primary_link",
        "secondary", "secondary_link", "tertiary", "tertiary_link",
    }
)  # fmt: skip
PROTECTED = frozenset(
    {"lane", "track", "shared_busway", "opposite_lane", "opposite_track", "separate"}
)
_LANE_KEYS = ("cycleway", "cycleway:right", "cycleway:left", "cycleway:both")
# Column positions in BRouter's ``messages`` table (row 0 is the header).
_DISTANCE, _WAY_TAGS = 3, 9


def _tags(raw: str) -> dict[str, str]:
    return dict(kv.split("=", 1) for kv in raw.split() if "=" in kv)


def main_road_share(messages: Any) -> float | None:
    """Share of the length on main roads without a bike lane, or ``None`` without tags."""
    if not isinstance(messages, list) or len(messages) < 2:
        return None
    total = unprotected = 0.0
    for row in messages[1:]:
        try:
            length, tags = float(row[_DISTANCE]), _tags(str(row[_WAY_TAGS]))
        except (IndexError, TypeError, ValueError):
            continue
        total += length
        if tags.get("highway") in MAIN_ROADS and tags.get("bicycle") != "designated":
            if not any(tags.get(key) in PROTECTED for key in _LANE_KEYS):
                unprotected += length
    return round(unprotected / total, 4) if total > 0 else None
