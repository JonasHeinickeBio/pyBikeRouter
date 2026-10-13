"""Tiles for a place: the ready-made areas, "around a town", and typed tile names."""

from __future__ import annotations

import pytest

from bike_routing_agent.providers.brouter_downloads import SEGMENT_NAME_RE
from bike_routing_agent.regions import (
    REGIONS,
    REGIONS_BY_KEY,
    parse_tiles,
    tile_name,
    tiles_around,
    tiles_covering,
)


def test_every_preset_names_real_looking_tiles_once_and_is_not_empty():
    assert len({r.key for r in REGIONS}) == len(REGIONS)
    for region in REGIONS:
        assert region.tiles and len(set(region.tiles)) == len(region.tiles), region.key
        assert all(SEGMENT_NAME_RE.match(t) for t in region.tiles), region.key


@pytest.mark.parametrize(
    ("place", "lon", "lat", "region"),
    [
        ("Braunschweig", 10.5267, 52.2689, "germany"),
        ("Munich", 11.5755, 48.1374, "germany"),
        ("Freiburg", 7.8421, 47.9990, "germany"),
        ("Hamburg", 9.9937, 53.5511, "germany"),
        ("Vienna", 16.3738, 48.2082, "austria_switzerland"),
        ("Zurich", 8.5417, 47.3769, "austria_switzerland"),
        ("Amsterdam", 4.9041, 52.3676, "benelux"),
        ("Copenhagen", 12.5683, 55.6761, "denmark"),
        ("London", -0.1276, 51.5072, "great_britain"),
        ("Oxford", -1.2577, 51.7520, "great_britain"),
        ("Edinburgh", -3.1883, 55.9533, "great_britain"),
        ("Dublin", -6.2603, 53.3498, "ireland"),
        ("Paris", 2.3522, 48.8566, "france"),
        ("Rome", 12.4964, 41.9028, "italy"),
    ],
)
def test_a_citys_tile_is_in_its_countrys_preset(place, lon, lat, region):
    (tile,) = tiles_covering(lon, lat, lon, lat)
    assert tile in REGIONS_BY_KEY[region].tiles, (place, tile)


def test_around_a_town_names_the_one_to_four_tiles_of_a_day_trip():
    assert tiles_around(10.5267, 52.2689, 80) == ["E5_N50", "E10_N50"]  # near a tile border
    assert tiles_around(12.5, 52.5, 60) == ["E10_N50"]  # well inside one tile
    four = tiles_around(10.0, 50.0, 80)  # on a corner
    assert set(four) == {"E5_N45", "E5_N50", "E10_N45", "E10_N50"}
    assert tiles_around(-0.13, 51.5) == ["W5_N50", "E0_N50"]  # either side of Greenwich


def test_the_southern_and_western_hemispheres_are_named_with_s_and_w():
    assert tile_name(-5, -35) == "W5_S35" and tile_name(10, 50) == "E10_N50"
    # Buenos Aires: 35 S is the tile border, so 50 km around reach both rows
    assert set(tiles_around(-58.4, -34.6, 50)) == {"W60_S35", "W60_S40"}


@pytest.mark.parametrize("radius", [0, 0.5, 601, -5])
def test_a_silly_radius_is_refused(radius):
    with pytest.raises(ValueError):
        tiles_around(10, 50, radius)


def test_typed_tile_names_are_cleaned_and_checked():
    assert parse_tiles("e10_n50, W5_N50.rd5;E5_N45  e10_n50") == ["E10_N50", "W5_N50", "E5_N45"]
    for bad in ("", "  ", "Germany", "E10_X50", "E10_N50 oops"):
        with pytest.raises(ValueError):
            parse_tiles(bad)


def test_a_box_outside_the_world_is_refused():
    with pytest.raises(ValueError):
        tiles_covering(190, 0, 200, 10)


def test_a_box_that_ends_on_the_edge_of_the_world_names_no_tile_that_does_not_exist():
    east = tiles_around(179.9, 10.0, 100)  # near the antimeridian: E175 is the last column
    assert east and not any(t.startswith("E180") for t in east)
    assert set(tiles_covering(170, 0, 180, 5)) == {"E170_N0", "E175_N0", "E170_N5", "E175_N5"}
    north = tiles_around(10.0, 89.9, 200)  # next to the pole: N85 is the last row
    assert north and not any(t.endswith("N90") for t in north)
    assert tiles_covering(-180, -90, -170, -85)[0] == "W180_S90"
