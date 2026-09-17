import json

import pytest

from bike_routing_agent.exporters.geojson import to_geojson_feature, to_geojson_str
from bike_routing_agent.models import RouteCandidate, RouteMetrics


def make_candidate(geometry_type="LineString", coordinates=None) -> RouteCandidate:
    coordinates = coordinates or [[10.5, 52.3], [10.6, 52.4]]
    return RouteCandidate(
        provider="ors",
        provider_profile="cycling-regular",
        geometry_geojson={"type": geometry_type, "coordinates": coordinates},
        metrics=RouteMetrics(distance_m=1000, duration_s=200),
        score=0.8,
        warnings=["steep section"],
    )


def test_to_geojson_feature_preserves_linestring_geometry():
    candidate = make_candidate()
    feature = to_geojson_feature(candidate)
    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] == "LineString"
    assert feature["properties"]["provider"] == "ors"
    assert feature["properties"]["score"] == 0.8
    assert feature["properties"]["warnings"] == ["steep section"]


def test_to_geojson_feature_accepts_multilinestring():
    candidate = make_candidate(
        geometry_type="MultiLineString", coordinates=[[[10.5, 52.3], [10.6, 52.4]]]
    )
    feature = to_geojson_feature(candidate)
    assert feature["geometry"]["type"] == "MultiLineString"


def test_to_geojson_feature_rejects_unsupported_geometry():
    candidate = make_candidate(geometry_type="Point", coordinates=[10.5, 52.3])
    with pytest.raises(ValueError):
        to_geojson_feature(candidate)


def test_to_geojson_str_is_valid_json():
    candidate = make_candidate()
    parsed = json.loads(to_geojson_str(candidate))
    assert parsed["type"] == "Feature"
