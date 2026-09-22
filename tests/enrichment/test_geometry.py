"""Local-plane geometry helpers: distances, segments, and nearest-way matching."""

from __future__ import annotations

import math

import pytest

from bike_routing_agent.enrichment.geometry import (
    METERS_PER_DEGREE_LAT,
    ObservedWay,
    distance_m,
    match_segments_to_ways,
    point_segment_distance_m,
    point_to_way_distance_m,
    polyline_length_m,
    route_segments,
)
from bike_routing_agent.models import Coordinate

LAT = 52.5
LON_METERS_AT_525 = METERS_PER_DEGREE_LAT * math.cos(math.radians(LAT))


def lon_offset(lat: float, meters: float) -> Coordinate:
    """Point ``meters`` east of (13.4, lat) on the local plane."""
    degrees = meters / (METERS_PER_DEGREE_LAT * math.cos(math.radians(lat)))
    return Coordinate(lon=13.4 + degrees, lat=lat)


def test_distance_m_uses_equirectangular_scale() -> None:
    north = Coordinate(lon=13.4, lat=LAT + 0.01)
    assert distance_m(Coordinate(lon=13.4, lat=LAT), north) == pytest.approx(
        0.01 * METERS_PER_DEGREE_LAT, rel=1e-9
    )
    east = Coordinate(lon=13.41, lat=LAT)
    assert distance_m(Coordinate(lon=13.4, lat=LAT), east) == pytest.approx(
        0.01 * LON_METERS_AT_525, rel=1e-6
    )


def test_polyline_length_and_segments() -> None:
    points = [Coordinate(lon=13.4, lat=LAT), lon_offset(LAT, 100.0), lon_offset(LAT, 250.0)]
    segments = route_segments(points)
    assert len(segments) == 2
    assert sum(s.length_m for s in segments) == pytest.approx(polyline_length_m(points))
    assert segments[1].length_m == pytest.approx(150.0, rel=1e-6)
    mid = segments[0].midpoint
    assert mid.lat == pytest.approx(LAT)
    assert distance_m(Coordinate(lon=13.4, lat=LAT), mid) == pytest.approx(50.0, rel=1e-6)


def test_point_segment_distance_projects_and_clamps() -> None:
    a = Coordinate(lon=13.4, lat=LAT)
    b = lon_offset(LAT, 100.0)
    # perpendicular drop onto the segment
    point = Coordinate(lon=a.lon + 50.0 / LON_METERS_AT_525, lat=LAT + 10.0 / METERS_PER_DEGREE_LAT)
    assert point_segment_distance_m(point, a, b) == pytest.approx(10.0, rel=1e-6)
    # beyond the end the distance clamps to the endpoint
    beyond = Coordinate(lon=b.lon + 50.0 / LON_METERS_AT_525, lat=LAT)
    assert point_segment_distance_m(beyond, a, b) == pytest.approx(50.0, rel=1e-6)
    # degenerate zero-length segment
    assert point_segment_distance_m(point, a, a) == pytest.approx(
        math.hypot(50.0, 10.0), rel=1e-6
    )


def test_point_to_way_distance_uses_nearest_polyline_element() -> None:
    way = ObservedWay(
        way_id=1,
        tags={},
        points=[Coordinate(lon=13.4, lat=LAT), lon_offset(LAT, 100.0), lon_offset(LAT, 200.0)],
    )
    near_second_leg = Coordinate(
        lon=13.4 + 150.0 / LON_METERS_AT_525, lat=LAT + 3.0 / METERS_PER_DEGREE_LAT
    )
    assert point_to_way_distance_m(near_second_leg, way) == pytest.approx(3.0, rel=1e-6)

    single_point_way = ObservedWay(way_id=2, tags={}, points=[Coordinate(lon=13.4, lat=LAT)])
    assert point_to_way_distance_m(
        Coordinate(lon=13.4, lat=LAT + 0.001), single_point_way
    ) == pytest.approx(0.001 * METERS_PER_DEGREE_LAT, rel=1e-9)

    empty_way = ObservedWay(way_id=3, tags={}, points=[])
    assert point_to_way_distance_m(Coordinate(lon=13.4, lat=LAT), empty_way) == math.inf


def test_match_segments_to_ways_picks_nearest_within_tolerance() -> None:
    route = [Coordinate(lon=13.4, lat=LAT), lon_offset(LAT, 100.0), lon_offset(LAT, 200.0)]
    segments = route_segments(route)
    on_route = ObservedWay(
        way_id=10, tags={}, points=[Coordinate(lon=13.4, lat=LAT), lon_offset(LAT, 200.0)]
    )
    parallel_10m = ObservedWay(
        way_id=11,
        tags={},
        points=[
            Coordinate(lon=13.4, lat=LAT + 10.0 / METERS_PER_DEGREE_LAT),
            lon_offset(LAT + 10.0 / METERS_PER_DEGREE_LAT, 200.0),
        ],
    )
    matches = match_segments_to_ways(segments, [parallel_10m, on_route], tolerance_m=50.0)
    assert [m.way_id if m else None for m in matches] == [10, 10]


def test_match_segments_to_ways_rejects_far_ways() -> None:
    route = [Coordinate(lon=13.4, lat=LAT), lon_offset(LAT, 100.0)]
    segments = route_segments(route)
    far_away = ObservedWay(
        way_id=12,
        tags={},
        points=[
            Coordinate(lon=13.4, lat=LAT + 200.0 / METERS_PER_DEGREE_LAT),
            lon_offset(LAT + 200.0 / METERS_PER_DEGREE_LAT, 100.0),
        ],
    )
    assert match_segments_to_ways(segments, [far_away], tolerance_m=50.0) == [None]


def test_match_segments_to_ways_bbox_prefilter_skips_distant_ways() -> None:
    # A way a full degree east is far outside both the tolerance and the
    # grown bbox; correctness of the prefilter means it can never win.
    route = [Coordinate(lon=13.4, lat=LAT), Coordinate(lon=13.401, lat=LAT)]
    segments = route_segments(route)
    far_east = ObservedWay(
        way_id=13,
        tags={},
        points=[Coordinate(lon=14.4, lat=LAT), Coordinate(lon=14.401, lat=LAT)],
    )
    assert match_segments_to_ways(segments, [far_east], tolerance_m=1e9) == [None]
