from xml.etree import ElementTree as ET

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
