"""What the OSM tags BRouter routed over say about a route.

BRouter's GeoJSON carries a ``messages`` table with the way tags of every
segment it chose, i.e. exactly the data its profile priced. The only thing
derived from it here is the share of the route on main roads without a bike
lane (``RouteMetrics.main_road_share``): a proxy for motor-traffic exposure,
stated as such wherever it is shown.
"""

from __future__ import annotations

from typing import Any

from bike_routing_agent.config import SURFACE_TAXONOMY
from bike_routing_agent.models import RouteStretch

MAIN_ROADS = frozenset(
    {
        "trunk", "trunk_link", "primary", "primary_link",
        "secondary", "secondary_link", "tertiary", "tertiary_link",
    }
)  # fmt: skip
PROTECTED = frozenset(
    {"lane", "track", "shared_busway", "opposite_lane", "opposite_track", "separate"}
)
# Below this share of the length carrying any way tags, no share is stated.
MIN_TAGGED_SHARE = 0.8
_LANE_KEYS = ("cycleway", "cycleway:right", "cycleway:left", "cycleway:both")
# Column positions in BRouter's ``messages`` table (row 0 is the header).
_DISTANCE, _WAY_TAGS = 3, 9


def _tags(raw: str) -> dict[str, str]:
    return dict(kv.split("=", 1) for kv in raw.split() if "=" in kv)


def main_road_share(messages: Any) -> float | None:
    """Share of the length on main roads without a bike lane, or ``None`` without tags."""
    if not isinstance(messages, list) or len(messages) < 2:
        return None
    total = tagged = unprotected = 0.0
    for row in messages[1:]:
        try:
            length, tags = float(row[_DISTANCE]), _tags(str(row[_WAY_TAGS] or ""))
        except (IndexError, TypeError, ValueError):
            continue
        total += length
        if not tags:
            continue  # no way tags: not evidence of a quiet road, so not measured length
        tagged += length
        if tags.get("highway") in MAIN_ROADS and tags.get("bicycle") != "designated":
            if not any(tags.get(key) in PROTECTED for key in _LANE_KEYS):
                unprotected += length
    if total <= 0 or tagged / total < MIN_TAGGED_SHARE:
        return None  # too little tag data to say; unknown, not 0 %
    return round(unprotected / tagged, 4)


# --- traffic exposure ------------------------------------------------------------------------
# OSM has no traffic counts, so exposure is *estimated* per segment from what is mapped: the road
# class sets a base weight (0 = nothing motorised to speak of, 1 = a main road), then the tags
# change it -- a separate cycle track, a low speed limit, or a way closed to cars lowers it, a
# high limit raises it. The weights are judgement, not measurement; the result is reported in four
# bands and always worded as an estimate (see docs/api.md).
HIGHWAY_WEIGHT = {
    "motorway": 1.0, "motorway_link": 1.0, "trunk": 1.0, "trunk_link": 0.95,
    "primary": 0.9, "primary_link": 0.85, "secondary": 0.75, "secondary_link": 0.7,
    "tertiary": 0.5, "tertiary_link": 0.45, "unclassified": 0.25, "residential": 0.15,
    "road": 0.25, "living_street": 0.05, "service": 0.05,
    "track": 0.0, "path": 0.0, "cycleway": 0.0, "footway": 0.0, "pedestrian": 0.0,
    "bridleway": 0.0, "steps": 0.0, "corridor": 0.0,
}  # fmt: skip
UNKNOWN_HIGHWAY_WEIGHT = 0.2  # a road class that is not listed: some traffic, not none
SEPARATE_BIKE_WAY = frozenset({"track", "separate", "opposite_track"})
PAINTED_LANE = frozenset({"lane", "opposite_lane", "shared_busway"})
CLOSED_TO_CARS = frozenset({"no", "agricultural", "forestry", "private", "delivery", "destination"})
# Band edges on the per-segment weight: busy >= 0.6 > some >= 0.2 > quiet > 0 = off_road.
BUSY_FROM, SOME_FROM = 0.6, 0.2
TRAFFIC_BANDS = ("busy", "some", "quiet", "off_road")


def _speed(raw: str | None) -> float | None:
    """A ``maxspeed`` value in km/h; ``None`` when it is not a plain number (``walk``, ``none``,
    ``DE:urban`` ...). Miles are converted."""
    if not raw:
        return None
    text = raw.strip().lower()
    mph = text.endswith("mph")
    digits = text.removesuffix("mph").strip()
    try:
        value = float(digits)
    except ValueError:
        return None
    return value * 1.609 if mph else value


def segment_exposure(tags: dict[str, str]) -> float | None:
    """The estimated exposure (0..1) of one way segment; ``None`` when it has no ``highway`` tag
    (no evidence either way)."""
    highway = tags.get("highway")
    if not highway:
        return None
    weight = HIGHWAY_WEIGHT.get(highway, UNKNOWN_HIGHWAY_WEIGHT)
    if weight == 0:
        return 0.0
    if tags.get("bicycle") == "designated" or tags.get("highway") == "cycleway":
        return 0.0  # a way made for bikes, even where cars may also use it (rare): not a road
    if tags.get("motor_vehicle") in CLOSED_TO_CARS or tags.get("access") in CLOSED_TO_CARS:
        weight *= 0.1
    lanes = [tags.get(key) for key in _LANE_KEYS]
    if any(value in SEPARATE_BIKE_WAY for value in lanes):
        weight *= 0.15  # a cycle track beside the road: the road's cars are on the other side
    elif any(value in PAINTED_LANE for value in lanes):
        weight *= 0.5
    speed = _speed(tags.get("maxspeed"))
    if speed is not None:
        if speed <= 30:
            weight *= 0.6
        elif speed >= 80:
            weight *= 1.4
        elif speed >= 60:
            weight *= 1.2
    return min(weight, 1.0)


def traffic_band(exposure: float) -> str:
    """The band (``busy`` / ``some`` / ``quiet`` / ``off_road``) of an estimated exposure."""
    if exposure >= BUSY_FROM:
        return "busy"
    if exposure >= SOME_FROM:
        return "some"
    return "quiet" if exposure > 0 else "off_road"


def traffic_summary(messages: Any) -> tuple[dict[str, float], float | None]:
    """``(length shares by traffic band, mean exposure)`` -- the bands are ``busy`` / ``some`` /
    ``quiet`` / ``off_road``, summing to 1; ``({}, None)`` when too little of the route carries
    way tags (unknown, not "no traffic")."""
    if not isinstance(messages, list) or len(messages) < 2:
        return {}, None
    total = tagged = weighted = 0.0
    bands = dict.fromkeys(TRAFFIC_BANDS, 0.0)
    for row in messages[1:]:
        try:
            length, tags = float(row[_DISTANCE]), _tags(str(row[_WAY_TAGS] or ""))
        except (IndexError, TypeError, ValueError):
            continue
        total += length
        exposure = segment_exposure(tags)
        if exposure is None:
            continue
        tagged += length
        weighted += exposure * length
        bands[traffic_band(exposure)] += length
    if total <= 0 or tagged / total < MIN_TAGGED_SHARE:
        return {}, None
    shares = {band: round(length / tagged, 4) for band, length in bands.items()}
    return shares, round(weighted / tagged, 4)


def traffic_shares(messages: Any) -> dict[str, float]:
    """Just the band shares of :func:`traffic_summary` (``{}`` without enough way tags)."""
    return traffic_summary(messages)[0]


_UNPAVED = frozenset({"compacted", "loose", "natural_soft"})


def surface_class(tags: dict[str, str]) -> str:
    """``paved`` / ``unpaved`` / ``cobbles`` / ``unknown`` (no usable ``surface`` tag)."""
    raw = tags.get("surface")
    category = SURFACE_TAXONOMY.get(raw.split(":")[0]) if raw else None
    if category == "paved":
        return "paved"
    if category in _UNPAVED:
        return "unpaved"
    return "cobbles" if category == "masonry" else "unknown"


def surface_shares(messages: Any) -> dict[str, float]:
    """Length shares by surface class from the ``surface`` tags: ``paved``, ``unpaved``,
    ``cobbles`` and ``unknown`` (no usable tag), summing to 1; ``{}`` without tags."""
    if not isinstance(messages, list) or len(messages) < 2:
        return {}
    totals = {"paved": 0.0, "unpaved": 0.0, "cobbles": 0.0, "unknown": 0.0}
    any_tags = False
    for row in messages[1:]:
        try:
            length, tags = float(row[_DISTANCE]), _tags(str(row[_WAY_TAGS] or ""))
        except (IndexError, TypeError, ValueError):
            continue
        any_tags = any_tags or bool(tags)
        totals[surface_class(tags)] += length
    total = sum(totals.values())
    # A route with no way tags at all is "no data", not "100 % unknown".
    return {k: round(v / total, 4) for k, v in totals.items()} if total > 0 and any_tags else {}


_LON, _LAT = 0, 1


def _vertex_keys(coordinates: Any) -> list[tuple[int, int]] | None:
    """Geometry vertices as BRouter writes them in ``messages`` (degrees * 1e6, rounded)."""
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return None
    keys: list[tuple[int, int]] = []
    for position in coordinates:
        try:
            keys.append((round(float(position[0]) * 1e6), round(float(position[1]) * 1e6)))
        except (IndexError, TypeError, ValueError):
            return None
    return keys


def stretches_from(messages: Any, coordinates: Any) -> list[RouteStretch]:
    """Where the surface and the traffic exposure change along the route, as runs of vertices.

    Every row of ``messages`` is a segment that ends at a vertex of the geometry and carries that
    segment's tags. Each row's end point is found in the geometry (walking forward, so repeated
    points do not confuse it); if one cannot be found the rows do not describe this geometry and
    the answer is empty rather than a guess. Neighbouring segments of the same surface and traffic
    band are merged. Traffic is "unknown" everywhere when too little of the route carries
    ``highway`` tags, as in :func:`traffic_summary`.
    """
    keys = _vertex_keys(coordinates)
    if keys is None or not isinstance(messages, list) or len(messages) < 2:
        return []
    shares, _ = traffic_summary(messages)
    traffic_known = bool(shares)
    pieces: list[tuple[int, int, str, str]] = []
    start = 0
    for row in messages[1:]:
        try:
            end_key = (int(row[_LON]), int(row[_LAT]))
            tags = _tags(str(row[_WAY_TAGS] or ""))
        except (IndexError, TypeError, ValueError):
            return []
        try:
            end = next(i for i in range(start, len(keys)) if keys[i] == end_key)
        except StopIteration:
            return []
        if end == start:
            continue  # a zero-length row (a node with its own tags): nothing to colour
        exposure = segment_exposure(tags) if traffic_known else None
        traffic = traffic_band(exposure) if exposure is not None else "unknown"
        pieces.append((start, end, surface_class(tags), traffic))
        start = end
    merged: list[RouteStretch] = []
    for from_index, to_index, surface, traffic in pieces:
        last = merged[-1] if merged else None
        if last and last.surface == surface and last.traffic == traffic:
            merged[-1] = last.model_copy(update={"to_index": to_index})
        else:
            merged.append(
                RouteStretch(
                    from_index=from_index,
                    to_index=to_index,
                    surface=surface,  # type: ignore[arg-type]
                    traffic=traffic,  # type: ignore[arg-type]
                )
            )
    return merged
