"""Where surface and traffic change along a BRouter route (runs of geometry vertices)."""

from __future__ import annotations

import pytest

from bike_routing_agent.providers.brouter_tags import stretches_from, surface_class, traffic_band

HEADER = [
    "Longitude", "Latitude", "Elevation", "Distance", "CostPerKm", "ElevCost", "TurnCost",
    "NodeCost", "InitialCost", "WayTags", "NodeTags", "Time", "Energy",
]  # fmt: skip

# Vertices 0.001 degrees apart along a meridian; vertex i is at lat 52 + i/1000.
LINE = [[10.0, 52.0 + i * 0.001] for i in range(8)]


def row(vertex: int, tags: str, distance: int = 100) -> list[str]:
    lat = round((52.0 + vertex * 0.001) * 1e6)
    return ["10000000", str(lat), "50", str(distance), "0", "0", "0", "0", "0", tags, "", "0", "0"]


PAVED_QUIET = "highway=residential surface=asphalt maxspeed=30"
PAVED_BUSY = "highway=secondary surface=asphalt maxspeed=70"
GRAVEL_OFF = "highway=track surface=compacted bicycle=designated"
COBBLES_QUIET = "highway=residential surface=sett"
UNTAGGED_SURFACE = "highway=residential"


def stretches(rows, line=LINE):
    return stretches_from([HEADER, *rows], line)


def as_tuples(result):
    return [(s.from_index, s.to_index, s.surface, s.traffic) for s in result]


def test_a_change_of_surface_or_traffic_starts_a_new_stretch():
    got = stretches(
        [row(2, PAVED_QUIET), row(4, PAVED_BUSY), row(6, GRAVEL_OFF), row(7, COBBLES_QUIET)]
    )
    assert as_tuples(got) == [
        (0, 2, "paved", "quiet"),
        (2, 4, "paved", "busy"),
        (4, 6, "unpaved", "off_road"),
        (6, 7, "cobbles", "quiet"),
    ]


def test_neighbouring_segments_of_the_same_kind_are_one_stretch():
    got = stretches(
        [row(1, PAVED_QUIET), row(2, PAVED_QUIET), row(3, PAVED_QUIET), row(7, PAVED_BUSY)]
    )
    assert as_tuples(got) == [(0, 3, "paved", "quiet"), (3, 7, "paved", "busy")]


def test_the_stretches_are_contiguous_and_cover_the_route():
    got = stretches([row(1, PAVED_QUIET), row(3, GRAVEL_OFF), row(7, PAVED_BUSY)])
    assert got[0].from_index == 0 and got[-1].to_index == 7
    assert all(a.to_index == b.from_index for a, b in zip(got, got[1:], strict=False))


def test_a_zero_length_row_colours_nothing():
    got = stretches([row(3, PAVED_QUIET), row(3, GRAVEL_OFF, 0), row(7, PAVED_QUIET)])
    assert as_tuples(got) == [(0, 7, "paved", "quiet")]


def test_a_missing_surface_tag_is_unknown_not_paved():
    got = stretches([row(4, UNTAGGED_SURFACE), row(7, PAVED_QUIET)])
    assert got[0].surface == "unknown" and got[0].traffic == "quiet"


def test_with_too_few_highway_tags_traffic_is_unknown_everywhere():
    # only 1 of 4 segments carries a highway tag: below the share the summary needs
    rows = [
        row(1, "surface=asphalt"),
        row(2, "surface=asphalt"),
        row(3, "surface=asphalt"),
        row(7, PAVED_QUIET, 100),
    ]
    got = stretches(rows)
    assert {s.traffic for s in got} == {"unknown"}
    assert got[0].surface == "paved"


def test_repeated_points_in_the_geometry_do_not_confuse_the_walk():
    line = [[10.0, 52.0], [10.0, 52.001], [10.0, 52.001], [10.0, 52.002], [10.0, 52.003]]
    rows = [
        ["10000000", "52001000", "0", "50", "0", "0", "0", "0", "0", PAVED_QUIET, "", "0", "0"],
        ["10000000", "52003000", "0", "50", "0", "0", "0", "0", "0", GRAVEL_OFF, "", "0", "0"],
    ]
    got = stretches_from([HEADER, *rows], line)
    assert as_tuples(got) == [(0, 1, "paved", "quiet"), (1, 4, "unpaved", "off_road")]


@pytest.mark.parametrize(
    ("messages", "line"),
    [
        (None, LINE),
        ([], LINE),
        ([HEADER], LINE),
        ("nope", LINE),
        ([HEADER, row(2, PAVED_QUIET)], None),
        ([HEADER, row(2, PAVED_QUIET)], [[10.0, 52.0]]),
        ([HEADER, row(2, PAVED_QUIET)], [[10.0, 52.0], ["x", 1]]),
        ([HEADER, row(2, PAVED_QUIET)], [[10.0, 52.0], [10.0]]),
        ([HEADER, ["x"]], LINE),
        ([HEADER, ["a", "b", "0", "1", "0", "0", "0", "0", "0", "", "", "0", "0"]], LINE),
        ([HEADER, ["99000000", "1", "0", "1", "0", "0", "0", "0", "0", "", "", "0", "0"]], LINE),
    ],
)
def test_rows_that_do_not_describe_the_geometry_give_no_stretches(messages, line):
    assert stretches_from(messages, line) == []


def test_a_row_that_moves_backwards_in_the_geometry_gives_none():
    assert stretches([row(5, PAVED_QUIET), row(2, PAVED_QUIET)]) == []


def test_the_classifiers_agree_with_the_shares():
    assert surface_class({"surface": "asphalt"}) == "paved"
    assert surface_class({"surface": "gravel"}) == "unpaved"
    assert surface_class({"surface": "paving_stones:lanes"}) in {"paved", "cobbles"}
    assert surface_class({"surface": "sett"}) == "cobbles"
    assert surface_class({}) == "unknown" and surface_class({"surface": "weird"}) == "unknown"
    assert [traffic_band(x) for x in (1.0, 0.6, 0.2, 0.0)][0] == "busy"
    assert traffic_band(0.0) == "off_road"


# ------------------------------------------------------------------------- through the adapter


@pytest.mark.parametrize("with_messages", [True, False])
async def test_the_adapter_puts_the_stretches_on_the_candidate(with_messages):
    import httpx
    import respx

    from bike_routing_agent.models import Coordinate, RouteConstraints, RoutingRequest
    from bike_routing_agent.providers.brouter import BRouterAdapter

    properties = {"track-length": "700", "total-time": "200"}
    if with_messages:
        properties["messages"] = [HEADER, row(3, PAVED_QUIET), row(7, GRAVEL_OFF)]
    body = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": LINE},
                "properties": properties,
            }
        ],
    }
    request = RoutingRequest(
        origin=Coordinate(lon=10.0, lat=52.0),
        destination=Coordinate(lon=10.0, lat=52.007),
        constraints=RouteConstraints(bike_type="gravel"),
    )
    with respx.mock:
        respx.get("http://brouter.test/brouter").mock(return_value=httpx.Response(200, json=body))
        candidate = await BRouterAdapter(base_url="http://brouter.test").route(request)
    if with_messages:
        assert as_tuples(candidate.stretches) == [
            (0, 3, "paved", "quiet"),
            (3, 7, "unpaved", "off_road"),
        ]
    else:
        assert candidate.stretches == []
