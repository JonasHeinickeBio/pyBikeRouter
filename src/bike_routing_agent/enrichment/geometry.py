"""Local-plane geometry helpers for route/way matching.

At the scales involved (route segments and buffer distances well under a
kilometre) an equirectangular projection around the working latitude is
accurate to a fraction of a percent, so there is no need for a geodetic
library in the prototype. The PostGIS production pipeline replaces these
with true geographies (see docs/enrichment.md).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from bike_routing_agent.models import Coordinate

METERS_PER_DEGREE_LAT = 111_320.0
_EPSILON = 1e-9


@dataclass(frozen=True)
class ObservedWay:
    """One OSM way with its tags and decoded polyline geometry."""

    way_id: int
    tags: dict
    points: Sequence[Coordinate]


@dataclass(frozen=True)
class RouteSegment:
    """One segment of a route polyline, with the midpoint used for matching."""

    start: Coordinate
    end: Coordinate
    midpoint: Coordinate
    length_m: float


def _meters_per_degree_lon(lat_deg: float) -> float:
    return max(METERS_PER_DEGREE_LAT * math.cos(math.radians(lat_deg)), _EPSILON)


def _to_plane(point: Coordinate, reference_lat: float) -> tuple[float, float]:
    return (
        point.lon * _meters_per_degree_lon(reference_lat),
        point.lat * METERS_PER_DEGREE_LAT,
    )


def distance_m(a: Coordinate, b: Coordinate) -> float:
    reference_lat = (a.lat + b.lat) / 2.0
    ax, ay = _to_plane(a, reference_lat)
    bx, by = _to_plane(b, reference_lat)
    return math.hypot(bx - ax, by - ay)


def polyline_length_m(points: Sequence[Coordinate]) -> float:
    return sum(distance_m(a, b) for a, b in zip(points, points[1:], strict=False))


def route_segments(points: Sequence[Coordinate]) -> list[RouteSegment]:
    segments: list[RouteSegment] = []
    for start, end in zip(points, points[1:], strict=False):
        segments.append(
            RouteSegment(
                start=start,
                end=end,
                midpoint=Coordinate(
                    lon=(start.lon + end.lon) / 2.0, lat=(start.lat + end.lat) / 2.0
                ),
                length_m=distance_m(start, end),
            )
        )
    return segments


def point_segment_distance_m(point: Coordinate, a: Coordinate, b: Coordinate) -> float:
    """Distance from ``point`` to segment ``a``-``b`` in the local plane."""
    reference_lat = (point.lat + a.lat + b.lat) / 3.0
    px, py = _to_plane(point, reference_lat)
    ax, ay = _to_plane(a, reference_lat)
    bx, by = _to_plane(b, reference_lat)

    dx = bx - ax
    dy = by - ay
    squared = dx * dx + dy * dy
    if squared <= _EPSILON:
        return math.hypot(px - ax, py - ay)

    t = ((px - ax) * dx + (py - ay) * dy) / squared
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def point_to_way_distance_m(point: Coordinate, way: ObservedWay) -> float:
    points = way.points
    if len(points) < 2:
        if not points:
            return math.inf
        return distance_m(point, points[0])
    return min(
        point_segment_distance_m(point, a, b) for a, b in zip(points, points[1:], strict=False)
    )


def _way_bbox(way: ObservedWay) -> tuple[float, float, float, float]:
    lons = [p.lon for p in way.points]
    lats = [p.lat for p in way.points]
    return min(lons), max(lons), min(lats), max(lats)


def _bbox_contains(bbox: tuple[float, float, float, float], point: Coordinate) -> bool:
    return bbox[0] <= point.lon <= bbox[1] and bbox[2] <= point.lat <= bbox[3]


def _grown_bbox(way: ObservedWay, tolerance_m: float) -> tuple[float, float, float, float]:
    """The way's bbox grown by exactly ``tolerance_m``, in degrees.

    Conservative by construction: any point within ``tolerance_m`` (local
    plane) of the way lies inside. Degrees-of-latitude scale uniformly, but
    a degree of longitude shrinks with |latitude|, so the longitude margin
    uses the largest |latitude| the grown bbox can reach -- the smallest
    metres-per-degree, hence the widest margin.
    """
    min_lon, max_lon, min_lat, max_lat = _way_bbox(way)
    lat_margin = tolerance_m / METERS_PER_DEGREE_LAT
    lon_ref_lat = max(abs(min_lat - lat_margin), abs(max_lat + lat_margin))
    lon_margin = tolerance_m / _meters_per_degree_lon(lon_ref_lat)
    return (
        min_lon - lon_margin,
        max_lon + lon_margin,
        min_lat - lat_margin,
        max_lat + lat_margin,
    )


def match_segments_to_ways(
    segments: Sequence[RouteSegment],
    ways: Sequence[ObservedWay],
    *,
    tolerance_m: float,
) -> list[ObservedWay | None]:
    """Nearest way within ``tolerance_m`` of each segment midpoint.

    Ways whose bounding box misses the midpoint are skipped before the
    exact point-polyline distance is computed -- the cheap stand-in for a
    spatial index until PostGIS takes over this job. The bbox growth is
    derived from ``tolerance_m`` itself, so the prefilter can never reject
    a way that the exact distance check would have accepted.
    """
    bboxes = [_grown_bbox(way, tolerance_m) for way in ways]

    matches: list[ObservedWay | None] = []
    for segment in segments:
        best_way: ObservedWay | None = None
        best_distance = tolerance_m
        for way, bbox in zip(ways, bboxes, strict=True):
            if not _bbox_contains(bbox, segment.midpoint):
                continue
            distance = point_to_way_distance_m(segment.midpoint, way)
            if distance <= best_distance:
                best_distance = distance
                best_way = way
        matches.append(best_way)
    return matches
