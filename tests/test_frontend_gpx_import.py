"""frontend/gpx-import.js (run under node): a GPX file becomes a route the page can show."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from bike_routing_agent.exporters.gpx import to_gpx_str
from bike_routing_agent.models import RouteCandidate, RouteMetrics

NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "src" / "bike_routing_agent" / "frontend"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

NS = 'xmlns="http://www.topografix.com/GPX/1/1"'


def js(expression: str, *args):
    """Evaluate `expression` with G = the module and a, b = the arguments, as JSON."""
    script = (
        f"const G = require({json.dumps(str(FRONTEND / 'gpx-import.js'))});\n"
        f"const [a, b] = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        f"process.stdout.write(JSON.stringify({expression}));"
    )
    result = subprocess.run(
        [NODE, "-e", script],
        input=json.dumps(list(args)),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def parse(xml: str) -> dict:
    return js("G.parse(a)", xml)


def refused(xml: str) -> str:
    return js(
        "(() => { try { G.parse(a); return null; } catch (e) { return G.errorText(e); } })()", xml
    )


def gpx(body: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" {NS}>{body}</gpx>'


def trk(*points: str, name: str = "") -> str:
    title = f"<name>{name}</name>" if name else ""
    return f"<trk>{title}<trkseg>{''.join(points)}</trkseg></trk>"


def pt(lat: float, lon: float, ele: float | None = None, time: str | None = None) -> str:
    inner = (f"<ele>{ele}</ele>" if ele is not None else "") + (
        f"<time>{time}</time>" if time else ""
    )
    return f'<trkpt lat="{lat}" lon="{lon}">{inner}</trkpt>'


def haversine_m(a, b) -> float:
    r = 6371008.8
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(
        math.radians(b[0] - a[0]) / 2
    ) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# -------------------------------------------------------------------------- the app's own files


def candidate() -> RouteCandidate:
    coords = [[10.5 + i * 0.001, 52.25 + i * 0.0005, 70.0 + (i % 3) * 8] for i in range(40)]
    return RouteCandidate(
        provider="brouter",
        provider_profile="gravel",
        geometry_geojson={"type": "LineString", "coordinates": coords},
        metrics=RouteMetrics(distance_m=1, duration_s=1, ascent_m=1),
        score=0.9,
    )


def test_a_file_the_server_wrote_comes_back_as_the_same_track():
    c = candidate()
    got = parse(to_gpx_str(c, name="My ride"))
    assert got["name"] == "My ride" and got["segments"] == 1 and got["kind"] == "track"
    assert got["points"] == c.geometry_geojson["coordinates"]
    line = c.geometry_geojson["coordinates"]
    expected = sum(haversine_m(a, b) for a, b in zip(line, line[1:], strict=False))
    assert got["distance_m"] == pytest.approx(expected, rel=1e-9)


def test_a_file_the_page_wrote_is_read_too():
    c = candidate()
    script = (
        f"const E = require({json.dumps(str(FRONTEND / 'export.js'))});\n"
        f"const G = require({json.dumps(str(FRONTEND / 'gpx-import.js'))});\n"
        f"const cand = JSON.parse(process.argv[1]);\n"
        "process.stdout.write(JSON.stringify(G.parse(E.gpx(cand, 'From the page'))));"
    )
    result = subprocess.run(
        [NODE, "-e", script, c.model_dump_json()], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    got = json.loads(result.stdout)
    assert got["name"] == "From the page" and len(got["points"]) == 40


# ------------------------------------------------------------------------------ reading XML


def test_namespaces_extensions_comments_entities_and_a_byte_order_mark_are_read_through():
    xml = (
        "\ufeff<?xml version='1.0'?>\n<!-- exported by a head unit -->"
        '<gpx version="1.1" creator="Garmin" xmlns="http://www.topografix.com/GPX/1/1" '
        'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">'
        "<metadata><name>Ignored: the track has a name</name></metadata>"
        "<trk><name><![CDATA[Harz & more <loop>]]></name><trkseg>"
        "<trkpt lat='52.0' lon='10.0'><ele>100</ele><extensions>"
        "<gpxtpx:TrackPointExtension><gpxtpx:hr>120</gpxtpx:hr></gpxtpx:TrackPointExtension>"
        "</extensions></trkpt>"
        "<trkpt lat='52.01' lon='10.01'><ele>101</ele></trkpt>"
        "</trkseg></trk></gpx>"
    )
    got = parse(xml)
    assert got["name"] == "Harz & more <loop>"
    assert got["points"] == [[10.0, 52.0, 100], [10.01, 52.01, 101]]


def test_the_name_comes_from_the_track_then_the_metadata_and_may_be_missing():
    assert parse(gpx(f"<metadata><name>Meta</name></metadata>{trk(pt(52, 10), pt(52.1, 10))}"))[
        "name"
    ] == "Meta"
    assert parse(gpx(trk(pt(52, 10), pt(52.1, 10), name="Track")))["name"] == "Track"
    assert parse(gpx(trk(pt(52, 10), pt(52.1, 10))))["name"] == ""


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("hello", "no <gpx> element"),
        ("", "no <gpx> element"),
        ('<kml><Placemark/></kml>', "no <gpx> element"),
        ('<!DOCTYPE gpx [<!ENTITY x "y">]><gpx/>', "DOCTYPE"),
        ('<gpx><trk><trkseg><trkpt lat="52" lon="10"><ele>', "not complete XML"),
        ("<gpx><trk></gpx>", "not well-formed"),
        ("<gpx></trk></gpx>", "not well-formed"),
        (gpx(""), "no track or route"),
        (gpx(trk(pt(52, 10))), "no track or route"),
        (gpx(trk(pt(95, 10), pt(52, 10))), "no track or route"),  # off the planet: left out
        (gpx(trk('<trkpt lat="x" lon="10"/>', pt(52, 10))), "no track or route"),
        (gpx(trk('<trkpt lon="10"/>', pt(52, 10))), "no track or route"),
    ],
)
def test_a_file_that_is_no_track_is_refused_with_a_sentence_for_a_person(text, message):
    assert message in refused(text)


def test_an_error_that_is_not_ours_is_not_shown_as_a_stack_trace():
    assert js("G.errorText(new TypeError('boom'))") == "That file could not be read."


# ---------------------------------------------------------------------- tracks and routes


def test_segments_are_joined_in_order_and_the_gap_is_said():
    xml = gpx(
        "<trk><trkseg>"
        + pt(52.0, 10.0)
        + pt(52.01, 10.0)
        + "</trkseg><trkseg>"
        + pt(52.2, 10.0)
        + pt(52.21, 10.0)
        + "</trkseg></trk>"
    )
    got = parse(xml)
    assert got["segments"] == 2 and len(got["points"]) == 4
    assert any("2 separate segments" in w and "straight line" in w for w in got["warnings"])
    # the straight line across the gap counts in the distance, as it is drawn
    assert got["distance_m"] > 20000


def test_a_planned_route_is_read_when_there_is_no_track():
    xml = gpx(
        '<rte><name>Planned</name><rtept lat="52.0" lon="10.0"/>'
        '<rtept lat="52.1" lon="10.1"/></rte>'
    )
    got = parse(xml)
    assert got["kind"] == "route" and got["name"] == "Planned" and len(got["points"]) == 2


def test_a_track_wins_over_a_route_in_the_same_file():
    xml = gpx(
        '<rte><rtept lat="1" lon="1"/><rtept lat="2" lon="2"/></rte>'
        + trk(pt(52, 10), pt(52.1, 10), pt(52.2, 10))
    )
    got = parse(xml)
    assert got["kind"] == "track" and len(got["points"]) == 3


def test_points_without_a_usable_position_are_left_out_and_counted():
    got = parse(gpx(trk(pt(52, 10), '<trkpt lat="91" lon="10"/>', pt(52.1, 10), pt(52.2, 10))))
    assert len(got["points"]) == 3
    assert any("1 point without a usable position was left out" in w for w in got["warnings"])


def test_waypoints_are_kept_with_their_names():
    xml = gpx(
        trk(pt(52, 10), pt(52.1, 10))
        + '<wpt lat="52.05" lon="10.0"><name>Cafe</name><desc>Open at 10</desc></wpt>'
        + '<wpt lat="95" lon="10"><name>Nowhere</name></wpt>'
    )
    assert parse(xml)["waypoints"] == [
        {"lon": 10.0, "lat": 52.05, "name": "Cafe", "description": "Open at 10"}
    ]


# -------------------------------------------------------------------------------- measuring


def test_the_distance_is_the_sum_of_the_legs():
    got = parse(gpx(trk(pt(52.0, 10.0), pt(52.1, 10.0), pt(52.1, 10.1))))
    expected = haversine_m([10.0, 52.0], [10.0, 52.1]) + haversine_m([10.0, 52.1], [10.1, 52.1])
    assert got["distance_m"] == pytest.approx(expected, rel=1e-9)


def test_climbing_counts_from_three_metres_so_noise_is_not_climbing():
    noise = [100, 101, 100, 101.5, 99.5, 100.5, 101, 100]  # a GPS wobbling on the flat
    flat = parse(gpx(trk(*[pt(52 + i * 0.001, 10, e) for i, e in enumerate(noise)])))
    assert flat["ascent_m"] == 0 and flat["descent_m"] == 0
    hill = [100, 110, 125, 120, 110, 100]  # a real climb of 25 m and a 25 m descent
    got = parse(gpx(trk(*[pt(52 + i * 0.001, 10, e) for i, e in enumerate(hill)])))
    assert got["ascent_m"] == pytest.approx(25) and got["descent_m"] == pytest.approx(25)


def test_a_long_climb_made_of_small_steps_is_counted():
    steps = [100 + 2 * i for i in range(21)]  # 2 m every point, 40 m in all
    got = parse(gpx(trk(*[pt(52 + i * 0.001, 10, e) for i, e in enumerate(steps)])))
    assert got["ascent_m"] == pytest.approx(40, abs=2)


def test_elevation_is_left_out_altogether_when_some_points_lack_it():
    got = parse(gpx(trk(pt(52, 10, 100), pt(52.1, 10), pt(52.2, 10, 120))))
    assert got["ascent_m"] is None and all(len(p) == 2 for p in got["points"])
    assert any("no elevation" in w for w in got["warnings"])
    none = parse(gpx(trk(pt(52, 10), pt(52.1, 10))))
    assert none["ascent_m"] is None and none["warnings"] == []  # none at all: nothing to warn about


def test_the_time_is_what_the_file_recorded_and_absent_without_timestamps():
    timed = parse(
        gpx(trk(pt(52, 10, time="2026-10-31T09:00:00Z"), pt(52.1, 10, time="2026-10-31T10:30:00Z")))
    )
    assert timed["duration_s"] == 5400
    assert parse(gpx(trk(pt(52, 10), pt(52.1, 10))))["duration_s"] is None
    backwards = parse(
        gpx(trk(pt(52, 10, time="2026-10-31T10:00:00Z"), pt(52.1, 10, time="2026-10-31T09:00:00Z")))
    )
    assert backwards["duration_s"] is None  # a clock that went backwards is no duration


def test_a_track_that_ends_where_it_began_is_a_loop():
    loop = parse(gpx(trk(pt(52.0, 10.0), pt(52.05, 10.05), pt(52.0001, 10.0001))))
    assert loop["loop"] is True
    assert parse(gpx(trk(pt(52.0, 10.0), pt(52.05, 10.05))))["loop"] is False


# ------------------------------------------------------------------- what the page is given


def test_the_response_has_the_shape_of_a_ready_plan_with_one_unscored_candidate():
    xml = gpx(trk(pt(52.0, 10.0, 100), pt(52.1, 10.0, 130), pt(52.2, 10.0, 90), name="Ride"))
    response = js("G.toResponse(G.parse(a), b)", xml, "C:\\Users\\me\\rides\\saturday.gpx")
    candidate = response["route"]
    assert response["status"] == "ready" and response["candidates"] == [candidate]
    assert response["errors"] == [] and response["artifacts"] == []
    assert candidate["provider"] == "gpx" and candidate["provider_profile"] == "Ride"
    assert candidate["score"] is None and candidate["rank"] is None
    assert candidate["geometry_geojson"]["type"] == "LineString"
    metrics = candidate["metrics"]
    assert metrics["distance_m"] == pytest.approx(22239, abs=5) and metrics["ascent_m"] == 30
    assert metrics["duration_s"] is None and metrics["descent_m"] == 40
    assert candidate["provenance"]["imported_from"] == "saturday"  # no folders, no extension
    assert "Imported \"Ride\": 22.2 km, 30 m up." in response["explanation"]
    assert "Nothing was routed" in response["explanation"]


def test_a_file_without_a_name_is_called_by_its_file_name():
    response = js("G.toResponse(G.parse(a), b)", gpx(trk(pt(52, 10), pt(52.1, 10))), "gravel.GPX")
    assert response["route"]["provider_profile"] == "gravel"
    anonymous = js("G.toResponse(G.parse(a))", gpx(trk(pt(52, 10), pt(52.1, 10))))
    assert anonymous["route"]["provider_profile"] == "imported track"


def test_the_recorded_time_is_said_to_include_the_stops():
    xml = gpx(
        trk(pt(52, 10, time="2026-10-31T09:00:00Z"), pt(52.1, 10, time="2026-10-31T11:05:00Z"))
    )
    explanation = js("G.toResponse(G.parse(a), 'x.gpx').explanation", xml)
    assert "2 h 05 min recorded, stops included" in explanation


def test_a_very_long_track_is_thinned_for_the_map_but_measured_in_full():
    n = 45000
    points = "".join(pt(52 + i * 0.00001, 10) for i in range(n))
    response = js("G.toResponse(G.parse(a), 'long.gpx')", gpx(trk(points)))
    drawn = response["route"]["geometry_geojson"]["coordinates"]
    assert len(drawn) <= 20000 + 1 and drawn[-1] == [10, 52 + (n - 1) * 0.00001]
    assert response["route"]["metrics"]["distance_m"] == pytest.approx(
        haversine_m([10, 52], [10, 52 + (n - 1) * 0.00001]), rel=1e-3
    )
    assert any("45000 points" in w for w in response["route"]["warnings"])


def test_waypoints_and_the_loop_flag_travel_with_the_response():
    xml = gpx(
        trk(pt(52.0, 10.0), pt(52.05, 10.05), pt(52.0, 10.0))
        + '<wpt lat="52.05" lon="10.05"><name>Turn</name></wpt>'
    )
    response = js("G.toResponse(G.parse(a), 'x.gpx')", xml)
    assert response["imported"]["loop"] is True
    assert response["imported"]["waypoints"][0]["name"] == "Turn"
    assert "1 waypoint from the file is marked on the map" in response["explanation"]


def test_a_file_that_is_too_big_is_refused_before_it_is_read():
    assert js("G.refusal({ size: 1000 })") is None
    big = js("G.refusal({ size: 30 * 1024 * 1024 })")
    assert "30 MB" in big and "25 MB" in big
    assert js("G.refusal(null)") == "No file was chosen."


# ------------------------------------------------------------------------- in the page


def read(name: str) -> str:
    return (FRONTEND / name).read_text()


def test_the_page_loads_the_module_before_the_app_and_has_a_button_and_a_file_input():
    html = read("index.html")
    assert html.index('src="gpx-import.js"') < html.index('src="app.js"')
    assert 'id="import-btn"' in html and 'id="import-input"' in html
    assert 'type="file"' in html and ".gpx" in html


def test_the_file_is_read_in_the_browser_and_never_sent_anywhere():
    app = read("app.js")
    start = app.index("async function importGpx(")
    body = app[start : app.index("function wireImport(", start)]
    assert "BikeGpxImport.parse(await file.text())" in body
    assert "fetch(" not in body and "FormData" not in body
    assert "state.lastResponse = null" in body  # the chat is not handed a route it never planned
    assert "state.formRouteUnsent = false" in body


def test_an_imported_track_cannot_be_reshaped_by_dragging():
    app = read("app.js")
    line = app[app.index("function activeRouteLine()") :][:400]
    assert 'provider === "gpx"' in line and "return null" in line


def test_a_file_can_be_dropped_on_the_map_and_resetting_clears_its_markers():
    app = read("app.js")
    wire = app[app.index("function wireImport()") : app.index("function renderErrors(")]
    assert '"dragover"' in wire and '"drop"' in wire and "dataTransfer.types" in wire
    assert "state.importLayer.clearLayers();" in app[app.index("function clearResults()") :][:700]
