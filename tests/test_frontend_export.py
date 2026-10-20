"""frontend/export.js (run under node) must produce what the Python exporters produce."""

import json
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from bike_routing_agent.exporters.geojson import to_geojson_feature
from bike_routing_agent.exporters.gpx import to_gpx_str
from bike_routing_agent.models import RouteCandidate, RouteMetrics

NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "src" / "bike_routing_agent" / "frontend"
GPX = "{http://www.topografix.com/GPX/1/1}"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_js(expression: str):
    script = (
        f"const E = require({json.dumps(str(FRONTEND / 'export.js'))});\n"
        f"process.stdout.write(JSON.stringify({expression}));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def candidate(geometry=None, **kw) -> RouteCandidate:
    return RouteCandidate(
        provider="brouter",
        provider_profile="custom_touring-v1",
        geometry_geojson=geometry
        or {"type": "LineString", "coordinates": [[10.5, 52.25, 73], [10.51, 52.26, 75.5]]},
        metrics=RouteMetrics(distance_m=22740, duration_s=7900, ascent_m=617),
        score=0.95,
        warnings=["a warning"],
        **kw,
    )


def points(xml: str):
    """Track points as numbers: JS prints 10 where Python prints 10.0, which is the same value."""
    root = ET.fromstring(xml.split("\n", 1)[1])
    out = []
    for p in root.iter(f"{GPX}trkpt"):
        ele = p.find(f"{GPX}ele")
        out.append(
            (float(p.get("lon")), float(p.get("lat")), None if ele is None else float(ele.text))
        )
    return out, root


def test_the_gpx_matches_the_servers_gpx():
    cand = candidate()
    mine = run_js(f"E.gpx({json.dumps(cand.model_dump(mode='json'))}, 'my route')")
    server = to_gpx_str(cand, name="my route")
    mine_points, mine_root = points(mine)
    server_points, server_root = points(server)
    assert mine_points == server_points == [(10.5, 52.25, 73.0), (10.51, 52.26, 75.5)]
    for root in (mine_root, server_root):
        assert root.get("version") == "1.1" and root.get("creator") == "bike-routing-agent"
        assert root.find(f"{GPX}metadata/{GPX}name").text == "my route"
        assert root.find(f"{GPX}trk/{GPX}name").text == "my route"
        assert (
            root.find(f"{GPX}metadata/{GPX}extensions/{GPX}provider_profile").text
            == "custom_touring-v1"
        )


def test_a_multilinestring_and_missing_elevation_match_the_server_too():
    geometry = {
        "type": "MultiLineString",
        "coordinates": [[[10.0, 52.0], [10.1, 52.1]], [[10.2, 52.2, 5], [10.3, 52.3, 6]]],
    }
    cand = candidate(geometry)
    mine_points, _ = points(run_js(f"E.gpx({json.dumps(cand.model_dump(mode='json'))})"))
    server_points, _ = points(to_gpx_str(cand))
    assert mine_points == server_points
    assert mine_points[0][2] is None and mine_points[2][2] == 5.0
    mine_root = points(run_js(f"E.gpx({json.dumps(cand.model_dump(mode='json'))})"))[1]
    assert len(list(mine_root.iter(f"{GPX}trkseg"))) == 2


def test_names_are_xml_escaped():
    cand = candidate()
    xml = run_js(f"E.gpx({json.dumps(cand.model_dump(mode='json'))}, '<b>&\"')")
    assert "<b>" not in xml and "&lt;b&gt;&amp;&quot;" in xml
    ET.fromstring(xml.split("\n", 1)[1])  # still well-formed


def test_the_geojson_matches_the_servers_feature():
    cand = candidate()
    mine = json.loads(run_js(f"E.geojson({json.dumps(cand.model_dump(mode='json'))})"))
    assert mine == to_geojson_feature(cand)


def test_unsupported_geometry_is_refused_like_the_server():
    cand = candidate().model_dump(mode="json")
    cand["geometry_geojson"] = {"type": "Point", "coordinates": [10, 52]}
    script = (
        f"const E = require({json.dumps(str(FRONTEND / 'export.js'))});\n"
        f"try {{ E.gpx({json.dumps(cand)}); }} catch (e) {{ process.stdout.write(e.message); }}"
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30).stdout
    assert "unsupported geometry type for export: Point" in out


def test_file_names_say_which_alternative_it_is_and_formats_have_mime_types():
    cand = candidate().model_dump(mode="json")
    assert run_js(f"E.filename({json.dumps(cand)}, 'gpx')") == "route-custom_touring-v1-22.7km.gpx"
    built = run_js(f"E.build({json.dumps(cand)}, 'geojson')")
    assert built["type"] == "application/geo+json" and built["filename"].endswith(".geojson")
    assert run_js(f"E.build({json.dumps(cand)}, 'gpx')")["type"] == "application/gpx+xml"
    odd = {**cand, "provider_profile": "weird profile/../x"}
    assert "/" not in run_js(f"E.filename({json.dumps(odd)}, 'gpx')")


# ------------------------------------------------------------------ the forecast in the files

from datetime import UTC, datetime  # noqa: E402

from bike_routing_agent.weather.models import (  # noqa: E402
    HourlyWeather,
    RouteWeather,
    WeatherSample,
    WeatherSummary,
    WindEffect,
)

T0 = datetime(2026, 10, 20, 12, 30, tzinfo=UTC)


def forecast_candidate(**heads) -> RouteCandidate:
    """Values chosen where Python's round() and JavaScript's Math.round() would disagree.
    ``head12``/``head13``/``head14``: the headwind (km/h) at the sample reached at that hour."""

    def at(fraction, lon, hour, **weather):
        return WeatherSample(
            fraction=fraction,
            lon=lon,
            lat=52.25,
            time=datetime(2026, 10, 20, hour, 30, tzinfo=UTC),
            weather=HourlyWeather(time=T0, condition="clear", **weather),
            headwind_kmh=heads.get(f"head{hour}"),
        )

    samples = [
        at(0.0, 10.5, 12, temperature_c=8.5, precipitation_probability=2.5,
           wind_speed_kmh=19.5, wind_from_deg=22.5),
        at(0.5, 10.1, 13, temperature_c=-0.5, precipitation_probability=60.0,
           wind_speed_kmh=7.5, wind_from_deg=337.5),
        at(1.0, 9.7, 14, temperature_c=-1.5, wind_from_deg=-90.0),  # no speed; a negative angle
    ]  # fmt: skip
    return candidate(
        weather=RouteWeather(
            provider="open-meteo",
            attribution='Weather data by Open-Meteo.com & "DWD" <CC BY>',
            departure=T0,
            arrival=datetime(2026, 10, 20, 15, 30, tzinfo=UTC),
            duration_source="provider",
            retrieved_at=T0,
            samples=samples,
            summary=WeatherSummary(temperature_min_c=-1.5, temperature_max_c=8.5),
            wind_effect=WindEffect(
                still_air_s=10800.0, adjusted_s=11880.0, effect_s=1080.0, range_s=(900.0, 1300.0)
            ),
            advisories=["A note."],
        )
    )


def waypoints(xml: str):
    root = ET.fromstring(xml.split("\n", 1)[1])
    out = []
    for w in root.findall(f"{GPX}wpt"):

        def text(tag, node=w):
            found = node.find(f"{GPX}{tag}")
            return None if found is None else found.text

        out.append((w.get("lon"), w.get("lat"), text("time"), text("name"), text("desc")))
    meta = root.find(f"{GPX}metadata/{GPX}desc")
    return out, None if meta is None else meta.text, [c.tag.removeprefix(GPX) for c in root]


def test_the_gpx_forecast_matches_the_server_waypoint_for_waypoint():
    cand = forecast_candidate(head12=12.5, head13=-3.0, head14=2.0)
    data = json.dumps(cand.model_dump(mode="json"))
    mine = waypoints(run_js(f"E.gpx({data}, 'x')"))
    server = waypoints(to_gpx_str(cand, name="x"))
    assert mine == server
    points, desc, order = server
    assert order == ["metadata", "wpt", "wpt", "wpt", "trk"]
    assert points[0][4] == "9 °C, rain 3 %, wind 20 km/h from NE, headwind 13 km/h"
    assert points[1][4] == "0 °C, rain 60 %, wind 8 km/h from N, tailwind 3 km/h"
    assert points[2][4] == "-1 °C"  # -1.5 rounds to -1 in both; no wind speed: no wind text
    assert "+18 min" in desc and desc.endswith('& "DWD" <CC BY>')  # the credit, unescaped again


def test_the_geojson_forecast_matches_the_servers_block():
    cand = forecast_candidate(head12=12.5, head13=-3.0)
    data = json.dumps(cand.model_dump(mode="json"))
    mine = json.loads(run_js(f"E.geojson({data})"))
    assert mine == to_geojson_feature(cand)
    assert mine["properties"]["forecast"]["points"][0]["headwind_kmh"] == 12.5


def test_a_route_without_a_forecast_still_matches_and_has_no_forecast_parts():
    cand = candidate()
    data = json.dumps(cand.model_dump(mode="json"))
    gpx_points, desc, order = waypoints(run_js(f"E.gpx({data})"))
    assert gpx_points == [] and desc is None and order == ["metadata", "trk"]
    assert "forecast" not in json.loads(run_js(f"E.geojson({data})"))["properties"]


def test_the_cues_are_in_the_geojson_the_same_in_both_and_absent_without_any():
    from bike_routing_agent.models import RouteCue

    cues = [
        RouteCue(kind="left", distance_m=120.5, lon=10.5, lat=52.25, angle_deg=-90),
        RouteCue(kind="roundabout", distance_m=900.0, lon=10.51, lat=52.26, exit=2, angle_deg=-124),
    ]
    cand = candidate(cues=cues)
    mine = json.loads(run_js(f"E.geojson({json.dumps(cand.model_dump(mode='json'))})"))
    assert mine == to_geojson_feature(cand)
    assert [c["kind"] for c in mine["properties"]["cues"]] == ["left", "roundabout"]
    plain = json.loads(run_js(f"E.geojson({json.dumps(candidate().model_dump(mode='json'))})"))
    assert (
        "cues" not in plain["properties"]
        and "cues" not in to_geojson_feature(candidate())["properties"]
    )


@pytest.mark.parametrize("degrees", [0, 22.4, 22.5, 44, 337.5, 359.9, 360, -90, -22.5, 720])
def test_the_compass_agrees_between_python_and_javascript(degrees):
    from bike_routing_agent.exporters.forecast import compass

    assert run_js(f"E.compass({degrees})") == compass(degrees)
