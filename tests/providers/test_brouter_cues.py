"""Turn-by-turn cues from BRouter's voicehints (codes read off real routes)."""

from __future__ import annotations

import httpx
import pytest
import respx

from bike_routing_agent.models import Coordinate, RouteConstraints, RoutingRequest
from bike_routing_agent.providers.brouter import BRouterAdapter
from bike_routing_agent.providers.brouter_cues import cues_from

# 11 vertices a little over 100 m apart along a meridian: vertex i is at about i * 111.2 m.
LINE = [[10.0, 52.0 + i * 0.001] for i in range(11)]
STEP_M = 111.2


def hint(index, command, exit_number=0, to_next=0.0, angle=0):
    return [index, command, exit_number, to_next, angle]


def cues(hints, line=LINE):
    return cues_from({"voicehints": hints}, line)


def test_each_command_becomes_its_kind():
    kinds = {
        2: "left",
        3: "slight_left",
        4: "sharp_left",
        5: "right",
        6: "slight_right",
        7: "sharp_right",
        8: "keep_left",
        9: "keep_right",
        10: "uturn",
        11: "uturn",
    }
    for command, kind in kinds.items():
        [cue] = cues([hint(3, command, angle=-90)])
        assert cue.kind == kind, command


def test_continue_off_route_beeline_and_unknown_codes_are_not_cues():
    assert cues([hint(1, 1), hint(2, 12), hint(3, 15), hint(4, 99), hint(5, 0), hint(6, -3)]) == []


def test_the_distance_is_measured_along_the_geometry_not_taken_from_the_hint():
    # the hint's own distance is "to the next hint" (real data), so it must not leak in
    [cue] = cues([hint(5, 5, to_next=9999.0, angle=83)])
    assert cue.distance_m == pytest.approx(5 * STEP_M, abs=2)
    assert (cue.lon, cue.lat) == (10.0, 52.005)
    assert cue.angle_deg == 83


def test_cues_keep_the_order_of_the_route():
    got = cues([hint(2, 2), hint(6, 5), hint(9, 9)])
    assert [c.kind for c in got] == ["left", "right", "keep_right"]
    assert [c.distance_m for c in got] == sorted(c.distance_m for c in got)


def test_a_roundabout_carries_its_exit():
    [cue] = cues([hint(4, 13, 2, angle=-124)])
    assert (cue.kind, cue.exit, cue.angle_deg) == ("roundabout", 2, -124)
    [left_driving] = cues([hint(4, 14, 3)])
    assert (left_driving.kind, left_driving.exit) == ("roundabout", 3)
    [no_exit] = cues([hint(4, 13, 0)])
    assert no_exit.exit is None  # no exit number: say "roundabout", not "exit 0"
    [absurd] = cues([hint(4, 13, 99)])
    assert absurd.exit is None


def test_an_exit_is_only_for_roundabouts():
    [cue] = cues([hint(4, 5, 2)])
    assert cue.exit is None


def test_bad_entries_are_skipped_not_fatal():
    hints = [
        "x",
        [],
        [1],
        [None, 5],
        [3, None],
        [3.0, "5"],  # a string command
        [99, 5],  # a vertex that does not exist
        [-1, 5],
        [True, 5],
        [float("nan"), 5],
        hint(4, 5, angle=999),  # kept, the angle is just not shown
        hint(7, 2),
    ]
    got = cues(hints)
    assert [(c.kind, c.angle_deg) for c in got] == [("right", None), ("left", 0)]


@pytest.mark.parametrize(
    ("properties", "line"),
    [
        ({}, LINE),
        ({"voicehints": None}, LINE),
        ({"voicehints": "nope"}, LINE),
        ({"voicehints": [hint(1, 2)]}, None),
        ({"voicehints": [hint(1, 2)]}, []),
        ({"voicehints": [hint(0, 2)]}, [[10.0, 52.0]]),  # one vertex is not a route
        ({"voicehints": [hint(1, 2)]}, [[10.0, 52.0], "x"]),
        ({"voicehints": [hint(1, 2)]}, [[10.0, 52.0], [10.0]]),
        ({"voicehints": [hint(1, 2)]}, [[10.0, 52.0], [float("inf"), 1]]),
    ],
)
def test_nothing_usable_is_no_cues(properties, line):
    assert cues_from(properties, line) == []


def test_elevation_in_the_coordinates_is_ignored():
    line = [[lon, lat, 50.0] for lon, lat in LINE]
    [cue] = cues([hint(5, 5)], line)
    assert cue.distance_m == pytest.approx(5 * STEP_M, abs=2)


# ------------------------------------------------------------------------- through the adapter

BASE = "http://brouter.test"


def request() -> RoutingRequest:
    return RoutingRequest(
        origin=Coordinate(lon=10.0, lat=52.0),
        destination=Coordinate(lon=10.0, lat=52.01),
        constraints=RouteConstraints(bike_type="gravel"),
    )


def payload(**properties):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": LINE},
                "properties": {"track-length": "1112", "total-time": "300", **properties},
            }
        ],
    }


@respx.mock
async def test_the_adapter_asks_for_turn_instructions_and_returns_the_cues():
    route = respx.get(f"{BASE}/brouter").mock(
        return_value=httpx.Response(200, json=payload(voicehints=[hint(3, 2, angle=-90)]))
    )
    candidate = await BRouterAdapter(base_url=BASE).route(request())
    assert route.calls[0].request.url.params["timode"] == "2"
    assert [(c.kind, c.angle_deg) for c in candidate.cues] == [("left", -90)]


@respx.mock
async def test_a_route_without_hints_has_no_cues_and_is_otherwise_unchanged():
    respx.get(f"{BASE}/brouter").mock(return_value=httpx.Response(200, json=payload()))
    candidate = await BRouterAdapter(base_url=BASE).route(request())
    assert candidate.cues == [] and candidate.metrics.distance_m == 1112.0
