from xml.etree import ElementTree as ET

import pytest

from bike_routing_agent.exporters.gpx import to_gpx_str
from bike_routing_agent.models import RouteCandidate, RouteMetrics

NS = "{http://www.topografix.com/GPX/1/1}"


def make_candidate() -> RouteCandidate:
    return RouteCandidate(
        provider="ors",
        provider_profile="cycling-regular",
        geometry_geojson={
            "type": "LineString",
            "coordinates": [[10.5, 52.3, 75.0], [10.6, 52.4, 80.0]],
        },
        metrics=RouteMetrics(distance_m=1000, duration_s=200),
    )


def test_gpx_export_is_parseable_and_contains_metadata():
    xml_str = to_gpx_str(make_candidate(), name="test-route")
    root = ET.fromstring(xml_str.split("\n", 1)[1])

    assert root.tag == f"{NS}gpx"
    provider = root.find(f"{NS}metadata/{NS}extensions/{NS}provider")
    profile = root.find(f"{NS}metadata/{NS}extensions/{NS}provider_profile")
    assert provider is not None and provider.text == "ors"
    assert profile is not None and profile.text == "cycling-regular"

    trkpts = root.findall(f"{NS}trk/{NS}trkseg/{NS}trkpt")
    assert len(trkpts) == 2
    assert trkpts[0].get("lon") == "10.5"
    assert trkpts[0].get("lat") == "52.3"
    ele = trkpts[0].find(f"{NS}ele")
    assert ele is not None and ele.text == "75.0"


def test_multi_line_string_geometry_produces_one_trkseg_per_line():
    candidate = make_candidate().model_copy(
        update={
            "geometry_geojson": {
                "type": "MultiLineString",
                "coordinates": [
                    [[10.5, 52.3], [10.6, 52.4]],
                    [[10.7, 52.5], [10.8, 52.6]],
                ],
            }
        }
    )
    root = ET.fromstring(to_gpx_str(candidate).split("\n", 1)[1])

    trksegs = root.findall(f"{NS}trk/{NS}trkseg")
    assert len(trksegs) == 2
    assert len(trksegs[0].findall(f"{NS}trkpt")) == 2
    assert len(trksegs[1].findall(f"{NS}trkpt")) == 2


def test_2d_points_omit_the_elevation_element():
    candidate = make_candidate().model_copy(
        update={"geometry_geojson": {"type": "LineString", "coordinates": [[10.5, 52.3]]}}
    )
    root = ET.fromstring(to_gpx_str(candidate).split("\n", 1)[1])

    trkpt = root.find(f"{NS}trk/{NS}trkseg/{NS}trkpt")
    assert trkpt is not None
    assert trkpt.find(f"{NS}ele") is None


def test_unsupported_geometry_type_raises_value_error():
    candidate = make_candidate().model_copy(
        update={"geometry_geojson": {"type": "Point", "coordinates": [10.5, 52.3]}}
    )
    with pytest.raises(ValueError, match="unsupported geometry type"):
        to_gpx_str(candidate)


def test_name_is_used_for_both_metadata_and_track():
    root = ET.fromstring(to_gpx_str(make_candidate(), name="my-route").split("\n", 1)[1])

    meta_name = root.find(f"{NS}metadata/{NS}name")
    trk_name = root.find(f"{NS}trk/{NS}name")
    assert meta_name is not None and meta_name.text == "my-route"
    assert trk_name is not None and trk_name.text == "my-route"
