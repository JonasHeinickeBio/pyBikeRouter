import pytest

from bike_routing_agent.providers.brouter_segments import (
    SEGMENTS_BASE_URL,
    missing_segment,
    segment_name,
    segments_for_points,
)


@pytest.mark.parametrize(
    ("lon", "lat", "name"),
    [
        (10.5267, 52.2689, "E10_N50"),  # Braunschweig
        (-1.25, 51.75, "W5_N50"),  # Oxford: what BRouter itself reported
        (10.5, 49.9, "E10_N45"),  # what BRouter itself reported south of 50 N
        (-0.0001, 0.0001, "W5_N0"),
        (0.0, 0.0, "E0_N0"),
        (-5.0, 50.0, "W5_N50"),  # the western edge belongs to the segment it opens
        (-5.0001, -0.1, "W10_S5"),
        (179.9, -89.9, "E175_S90"),
    ],
)
def test_segment_names_match_brouters_five_degree_grid(lon, lat, name):
    assert segment_name(lon, lat) == name


def test_the_segments_of_a_trip_are_distinct_and_in_travel_order():
    points = [(10.5, 52.2), (10.6, 52.3), (10.5, 49.9), (-1.2, 51.7)]
    assert segments_for_points(points) == ["E10_N50", "E10_N45", "W5_N50"]
    assert segments_for_points([]) == []


def test_the_missing_segment_is_read_from_brouters_message():
    assert missing_segment("datafile W5_N50.rd5 not found\n") == "W5_N50"
    assert missing_segment("DATAFILE e10_n45.RD5 NOT FOUND") == "E10_N45"
    assert missing_segment("no track found") is None
    assert missing_segment("datafile ../../etc/passwd.rd5 not found") is None


def test_the_official_download_location_is_https():
    assert SEGMENTS_BASE_URL.startswith("https://") and SEGMENTS_BASE_URL.endswith("/")
