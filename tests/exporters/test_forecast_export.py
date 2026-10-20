"""The forecast along the route, in the GPX and GeoJSON files."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import pytest

from bike_routing_agent.exporters import forecast
from bike_routing_agent.exporters.geojson import to_geojson_feature, to_geojson_str
from bike_routing_agent.exporters.gpx import to_gpx_str
from bike_routing_agent.models import RouteCandidate, RouteMetrics
from bike_routing_agent.weather.models import (
    HourlyWeather,
    RouteWeather,
    WeatherSample,
    WeatherSummary,
    WindEffect,
)

NS = "{http://www.topografix.com/GPX/1/1}"
T0 = datetime(2026, 10, 20, 12, 30, tzinfo=UTC)


def sample(fraction, lon, lat, hour, *, temp=8.5, rain=2.0, speed=19.4, from_deg=90.0, head=12.3):
    return WeatherSample(
        fraction=fraction,
        lon=lon,
        lat=lat,
        time=datetime(2026, 10, 20, hour, 30, tzinfo=UTC),
        bearing_deg=270.0,
        weather=HourlyWeather(
            time=datetime(2026, 10, 20, hour, 0, tzinfo=UTC),
            temperature_c=temp,
            precipitation_probability=rain,
            wind_speed_kmh=speed,
            wind_from_deg=from_deg,
            condition="clear",
        ),
        headwind_kmh=head,
    )


def weather(**kw) -> RouteWeather:
    samples = kw.pop("samples", None)
    if samples is None:
        samples = [
            sample(0.0, 10.5, 52.25, 12),
            sample(
                0.5, 10.1, 52.3, 13, temp=-0.5, rain=60.0, speed=22.0, from_deg=337.0, head=-8.0
            ),
            sample(1.0, 9.7, 52.4, 14, head=1.0),
        ]
    return RouteWeather(
        provider="open-meteo",
        attribution="Weather data by Open-Meteo.com (CC BY 4.0)",
        departure=T0,
        arrival=datetime(2026, 10, 20, 15, 30, tzinfo=UTC),
        duration_source="provider",
        retrieved_at=T0,
        samples=samples,
        summary=WeatherSummary(
            temperature_min_c=-0.5, temperature_max_c=8.5, wind_speed_max_kmh=22
        ),
        wind_effect=WindEffect(
            still_air_s=10800.0, adjusted_s=11520.0, effect_s=720.0, range_s=(540.0, 900.0)
        ),
        advisories=["A note."],
        **kw,
    )


def cand(w: RouteWeather | None = None) -> RouteCandidate:
    return RouteCandidate(
        provider="brouter",
        provider_profile="trekking",
        geometry_geojson={"type": "LineString", "coordinates": [[10.5, 52.25], [9.7, 52.4]]},
        metrics=RouteMetrics(distance_m=66000, duration_s=10800),
        weather=w,
    )


# --------------------------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    ("degrees", "expected"),
    [(0, "N"), (44, "NE"), (90, "E"), (135, "SE"), (180, "S"), (270, "W"), (337.5, "N"),
     (359, "N"), (-90, "W"), (360, "N"), (22.4, "N"), (22.5, "NE")],
)  # fmt: skip
def test_the_compass_has_eight_points_and_wraps(degrees, expected):
    assert forecast.compass(degrees) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(8.5, 9), (7.5, 8), (0.5, 1), (-0.5, 0), (-1.5, -1), (2.4, 2), (-2.6, -3)],
)
def test_rounding_is_half_up_like_javascript_not_half_to_even(value, expected):
    assert forecast.round_half_up(value) == expected  # Python's round(8.5) would give 8


def test_a_sample_is_described_in_one_line_with_only_what_is_known():
    data = weather().model_dump(mode="json")["samples"]
    assert forecast.sample_description(data[0]) == (
        "9 °C, rain 2 %, wind 19 km/h from E, headwind 12 km/h"
    )
    assert forecast.sample_description(data[1]) == (
        "0 °C, rain 60 %, wind 22 km/h from NW, tailwind 8 km/h"
    )
    assert forecast.sample_description(data[2]).endswith("wind 19 km/h from E")  # 1 km/h: across
    assert forecast.sample_description({"weather": {}}) == ""
    assert forecast.sample_name(data[0]) == "Forecast 12:30 UTC"


def test_the_wind_effect_text_is_signed_and_silent_when_negligible():
    assert forecast.wind_effect_text({"effect_s": 720}) == "+12 min"
    assert forecast.wind_effect_text({"effect_s": -600}) == "-10 min"
    assert forecast.wind_effect_text({"effect_s": 20}) is None
    assert forecast.wind_effect_text(None) is None and forecast.wind_effect_text({}) is None


def test_the_description_says_when_and_carries_the_data_credit():
    text = forecast.description(weather().model_dump(mode="json"))
    assert text == (
        "Forecast for a ride leaving 2026-10-20 12:30 UTC. "
        "Estimated effect of the wind on the time: +12 min. "
        "Weather data by Open-Meteo.com (CC BY 4.0)"
    )


def test_times_are_utc_whatever_zone_they_arrive_in():
    assert forecast.gpx_time("2026-10-20T14:30:00+02:00") == "2026-10-20T12:30:00Z"
    assert forecast.gpx_time(datetime(2026, 10, 20, 12, 30)) == "2026-10-20T12:30:00Z"  # naive: UTC
    assert forecast.clock("2026-10-20T12:30:00Z") == "12:30"


# --------------------------------------------------------------------------------------- GPX


def gpx_root(candidate: RouteCandidate) -> ET.Element:
    return ET.fromstring(to_gpx_str(candidate).split("\n", 1)[1])


def test_each_forecast_point_is_a_waypoint_with_time_name_and_description():
    root = gpx_root(cand(weather()))
    waypoints = root.findall(f"{NS}wpt")
    assert len(waypoints) == 3
    first = waypoints[0]
    assert (first.get("lon"), first.get("lat")) == ("10.5", "52.25")
    assert [child.tag.removeprefix(NS) for child in first] == [
        "time",
        "name",
        "desc",
    ]  # schema order
    assert first.find(f"{NS}time").text == "2026-10-20T12:30:00Z"
    assert first.find(f"{NS}name").text == "Forecast 12:30 UTC"
    assert first.find(f"{NS}desc").text.startswith("9 °C, rain 2 %")


def test_the_waypoints_come_before_the_track_and_the_metadata_has_the_description():
    root = gpx_root(cand(weather()))
    order = [child.tag.removeprefix(NS) for child in root]
    assert order == ["metadata", "wpt", "wpt", "wpt", "trk"]
    desc = root.find(f"{NS}metadata/{NS}desc").text
    assert "Open-Meteo.com" in desc and "+12 min" in desc
    meta = [child.tag.removeprefix(NS) for child in root.find(f"{NS}metadata")]
    assert meta == ["name", "desc", "extensions"]  # the schema's order


def test_a_point_with_nothing_to_say_has_no_description():
    bare = WeatherSample(fraction=0.0, lon=10.5, lat=52.25, time=T0, weather=HourlyWeather(time=T0))
    root = gpx_root(cand(weather(samples=[bare])))
    assert root.find(f"{NS}wpt/{NS}desc") is None
    assert root.find(f"{NS}wpt/{NS}name").text == "Forecast 12:30 UTC"


def test_a_route_without_a_forecast_exports_exactly_as_before():
    root = gpx_root(cand())
    assert [child.tag.removeprefix(NS) for child in root] == ["metadata", "trk"]
    assert root.find(f"{NS}metadata/{NS}desc") is None
    assert gpx_root(cand(weather(samples=[]))).find(f"{NS}wpt") is None  # no points: no forecast


def test_text_in_the_forecast_is_xml_escaped():
    risky = weather()
    risky.attribution = 'Data <by> & "others"'
    xml = to_gpx_str(cand(risky))
    assert "<by>" not in xml
    ET.fromstring(xml.split("\n", 1)[1])
    assert (
        'Data <by> & "others"'
        in ET.fromstring(xml.split("\n", 1)[1]).find(f"{NS}metadata/{NS}desc").text
    )


# ----------------------------------------------------------------------------------- GeoJSON


def test_the_geojson_carries_the_forecast_as_data_with_the_credit():
    props = to_geojson_feature(cand(weather()))["properties"]
    block = props["forecast"]
    assert block["attribution"] == "Weather data by Open-Meteo.com (CC BY 4.0)"
    assert block["departure"].startswith("2026-10-20T12:30") and block["advisories"] == ["A note."]
    assert block["summary"]["temperature_min_c"] == -0.5
    assert block["wind_effect"]["effect_s"] == 720.0
    first = block["points"][0]
    assert first["lon"] == 10.5 and first["temperature_c"] == 8.5
    assert first["wind_from_deg"] == 90.0 and first["headwind_kmh"] == 12.3
    assert first["condition"] == "clear" and len(block["points"]) == 3
    json.loads(to_geojson_str(cand(weather())))  # still valid JSON


def test_without_a_forecast_the_geojson_has_no_forecast_key():
    assert "forecast" not in to_geojson_feature(cand())["properties"]
    assert "forecast" not in to_geojson_feature(cand(weather(samples=[])))["properties"]
    assert set(to_geojson_feature(cand())["properties"]) == {
        "provider", "provider_profile", "distance_m", "duration_s",
        "ascent_m", "descent_m", "score", "warnings",
    }  # fmt: skip
