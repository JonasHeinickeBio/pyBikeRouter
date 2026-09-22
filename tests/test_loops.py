"""Unit tests for loop via synthesis (issue #5): the straight-line circuit
through the synthesized waypoints must hit the requested distance, the
direction must mirror the geometry, and degenerate inputs must raise."""

import json
import math

import pytest

from bike_routing_agent.loops import (
    SYNTHESIZED_VIA_COUNT,
    LoopPlan,
    synthesize_loop_vias,
)
from bike_routing_agent.models import Coordinate

ORIGIN = Coordinate(lon=10.52, lat=52.27)


def great_circle_m(a: Coordinate, b: Coordinate) -> float:
    """Haversine, for checking the synthesized circuit length."""
    r = 6_371_000.0
    phi1, phi2 = math.radians(a.lat), math.radians(b.lat)
    dphi = math.radians(b.lat - a.lat)
    dlmb = math.radians(b.lon - a.lon)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def circuit_m(plan: LoopPlan) -> float:
    points = [ORIGIN, *plan.vias, ORIGIN]
    return sum(great_circle_m(a, b) for a, b in zip(points, points[1:], strict=False))


def test_circuit_through_synthesized_vias_hits_the_target_distance():
    plan = synthesize_loop_vias(ORIGIN, 15_000)
    assert plan.sides == SYNTHESIZED_VIA_COUNT + 1
    assert len(plan.vias) == SYNTHESIZED_VIA_COUNT
    assert circuit_m(plan) == pytest.approx(15_000, rel=0.005)


def test_radius_is_the_circumradius_implied_by_the_target():
    plan = synthesize_loop_vias(ORIGIN, 15_000)
    # r = D / (2 * sides * sin(pi/sides)) for a regular polygon of chords.
    expected = 15_000 / (2 * 3 * math.sin(math.pi / 3))
    assert plan.radius_m == pytest.approx(expected, rel=1e-3)
    # The farthest via sits one polygon leg (the chord subtending one
    # central-angle step) from the start, which is what reach_m describes.
    chord = 2 * expected * math.sin(math.pi / plan.sides)
    farthest = max(great_circle_m(ORIGIN, v) for v in plan.vias)
    assert farthest == pytest.approx(chord, rel=0.01)
    assert plan.reach_m == pytest.approx(chord, rel=1e-3)


def test_direction_mirrors_the_circuit_across_the_east_west_axis():
    clockwise = synthesize_loop_vias(ORIGIN, 15_000, direction="clockwise")
    counter = synthesize_loop_vias(ORIGIN, 15_000, direction="counterclockwise")
    assert clockwise.direction == "clockwise"
    assert counter.direction == "counterclockwise"
    for cw, ccw in zip(clockwise.vias, counter.vias, strict=True):
        assert ccw.lon == pytest.approx(cw.lon, abs=1e-6)
        assert (ccw.lat - ORIGIN.lat) == pytest.approx(-(cw.lat - ORIGIN.lat), abs=1e-6)


def test_vias_are_valid_coordinates_with_bounded_precision():
    plan = synthesize_loop_vias(ORIGIN, 80_000)
    for via in plan.vias:
        assert -90 <= via.lat <= 90
        assert -180 <= via.lon <= 180
        assert abs(round(via.lat, 7) - via.lat) < 1e-12


def test_loops_at_the_antimeridian_wrap_into_valid_longitudes():
    plan = synthesize_loop_vias(Coordinate(lon=179.95, lat=0.0), 40_000)
    assert all(-180 <= v.lon <= 180 for v in plan.vias)


def test_polar_origins_are_refused_rather_than_degenerate():
    with pytest.raises(ValueError, match="unsupported"):
        synthesize_loop_vias(Coordinate(lon=10.0, lat=89.9), 10_000)


@pytest.mark.parametrize("target", [0, -1_000])
def test_non_positive_target_distance_is_an_error(target: float):
    with pytest.raises(ValueError, match="must be > 0"):
        synthesize_loop_vias(ORIGIN, target)


def test_unknown_direction_is_refused_defensively():
    with pytest.raises(ValueError, match="unknown loop direction"):
        synthesize_loop_vias(ORIGIN, 10_000, direction="sideways")  # type: ignore[arg-type]


def test_loop_plan_serializes_to_json_safe_state():
    plan = synthesize_loop_vias(ORIGIN, 12_000, direction="counterclockwise")
    state = plan.to_state()
    assert json.loads(json.dumps(state)) == state
    assert state["direction"] == "counterclockwise"
    assert state["sides"] == 3
    assert len(state["vias"]) == SYNTHESIZED_VIA_COUNT
