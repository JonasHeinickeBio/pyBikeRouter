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
