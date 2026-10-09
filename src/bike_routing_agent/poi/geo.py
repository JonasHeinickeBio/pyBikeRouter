"""Small planar geometry for POIs relative to a route.

An equirectangular projection around the working latitude is accurate to a
fraction of a percent at the distances involved (a route corridor of a few
kilometres), which is all "how far off the route is this" needs.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

METERS_PER_DEGREE_LAT = 111_320.0

# (lon, lat)
LonLat = tuple[float, float]


def decimate(points: Sequence[LonLat], max_points: int) -> list[LonLat]:
    """At most ``max_points`` points, evenly spread, first and last kept."""
    if len(points) <= max_points:
        return list(points)
    step = (len(points) - 1) / (max_points - 1)
    picked = [points[round(i * step)] for i in range(max_points)]
    picked[-1] = points[-1]
    return picked


def _plane(point: LonLat, lat0: float) -> tuple[float, float]:
    return (
        point[0] * METERS_PER_DEGREE_LAT * math.cos(math.radians(lat0)),
        point[1] * METERS_PER_DEGREE_LAT,
    )


def haversine_m(a: LonLat, b: LonLat) -> float:
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, math.radians(b[0] - a[0])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6_371_000.0 * math.asin(math.sqrt(h))


def cumulative_lengths_m(line: Sequence[LonLat]) -> list[float]:
    """Distance from the start to each vertex."""
    out = [0.0]
    for a, b in zip(line, line[1:], strict=False):
        out.append(out[-1] + haversine_m(a, b))
    return out


def project_onto_line(
    point: LonLat, line: Sequence[LonLat], cumulative_m: Sequence[float]
) -> tuple[float, float]:
    """``(offset_m, along_m)``: distance from the line, and the distance along it
    to the closest point."""
    if len(line) < 2:
        raise ValueError("a line needs at least two points")
    lat0 = point[1]
    px, py = _plane(point, lat0)
    best_offset = math.inf
    best_along = 0.0
    for i in range(len(line) - 1):
        ax, ay = _plane(line[i], lat0)
        bx, by = _plane(line[i + 1], lat0)
        dx, dy = bx - ax, by - ay
        seg_sq = dx * dx + dy * dy
        t = 0.0 if seg_sq == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_sq))
        offset = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        if offset < best_offset:
            best_offset = offset
            best_along = cumulative_m[i] + t * (cumulative_m[i + 1] - cumulative_m[i])
    return best_offset, best_along


# (min_lon, min_lat, max_lon, max_lat)
BBox = tuple[float, float, float, float]


def corridor_boxes(
    line: Sequence[LonLat], pad_m: float, *, max_boxes: int = 40, start_box_km: float = 10.0
) -> list[BBox]:
    """Bounding boxes that together cover everything within ``pad_m`` of the polyline.

    A box lookup uses Overpass's spatial index; an ``around`` filter over a long polyline
    does not, and a union of those for every category times out on the public instances.
    The boxes over-cover a diagonal stretch; callers measure the exact distance afterwards.
    Chunks grow until at most ``max_boxes`` are needed, so a very long route stays bounded.
    """
    if len(line) < 2:
        raise ValueError("a line needs at least two points")
    size_km = start_box_km
    while True:
        chunks = _chunks(_densify(line, size_km * 1000 / 2), size_km)
        if len(chunks) <= max_boxes:
            break
        size_km *= 1.5
    boxes: list[BBox] = []
    for chunk in chunks:
        lons = [p[0] for p in chunk]
        lats = [p[1] for p in chunk]
        mid_lat = (min(lats) + max(lats)) / 2
        dlat = pad_m / METERS_PER_DEGREE_LAT
        dlon = pad_m / (METERS_PER_DEGREE_LAT * max(math.cos(math.radians(mid_lat)), 1e-6))
        boxes.append((min(lons) - dlon, min(lats) - dlat, max(lons) + dlon, max(lats) + dlat))
    return boxes


def _chunks(line: Sequence[LonLat], size_km: float) -> list[list[LonLat]]:
    """Consecutive pieces of the line whose own extent stays within ~``size_km``."""
    limit_m = size_km * 1000
    chunks: list[list[LonLat]] = []
    current = [line[0]]
    for point in line[1:]:
        candidate = [*current, point]
        lons = [p[0] for p in candidate]
        lats = [p[1] for p in candidate]
        width = (max(lons) - min(lons)) * METERS_PER_DEGREE_LAT * math.cos(math.radians(lats[0]))
        height = (max(lats) - min(lats)) * METERS_PER_DEGREE_LAT
        if max(width, height) > limit_m and len(current) >= 2:
            chunks.append(current)
            current = [current[-1], point]
        else:
            current = candidate
    if len(current) >= 2 or not chunks:
        chunks.append(current if len(current) >= 2 else [*current, current[0]])
    return chunks


def _densify(line: Sequence[LonLat], max_segment_m: float) -> list[LonLat]:
    """Split segments longer than ``max_segment_m`` so one long straight leg gets several boxes
    (a single box around a 50 km diagonal would cover 2500 km2)."""
    out = [line[0]]
    for a, b in zip(line, line[1:], strict=False):
        pieces = max(1, math.ceil(haversine_m(a, b) / max_segment_m))
        for i in range(1, pieces + 1):
            out.append((a[0] + (b[0] - a[0]) * i / pieces, a[1] + (b[1] - a[1]) * i / pieces))
    return out
