"""The elevation profile helpers (frontend/profile.js), run under node."""

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "src" / "bike_routing_agent" / "frontend"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

STEP_M = 22.239  # one vertex every 0.0002 degrees of latitude


def js(expression: str, **data):
    """Evaluate ``expression`` with ``P`` (the module) and the given JSON values in scope."""
    names = "".join(f"const {k} = {json.dumps(v)};\n" for k, v in data.items())
    script = (
        f"const P = require({json.dumps(str(FRONTEND / 'profile.js'))});\n{names}"
        f"process.stdout.write(JSON.stringify({expression}));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def route(elevation, vertices=None, lon=10.0, lat=52.0):
    """A route running due north with ``elevation(distance_m)`` at every vertex."""
    n = vertices or 2
    coords = []
    for i in range(n):
        d = i * STEP_M
        coords.append([lon, lat + i * 0.0002, elevation(d)])
    return {"type": "LineString", "coordinates": coords}


def long_route(elevation, km):
    return route(elevation, vertices=int(km * 1000 / STEP_M) + 1)


# ----------------------------------------------------------------------------------- build


def test_a_profile_needs_elevation_on_every_vertex_and_some_length():
    good = long_route(lambda d: 100.0, 2)
    assert js("P.build(g) !== null", g=good) is True
    holes = json.loads(json.dumps(good))
    del holes["coordinates"][5][2]
    assert js("P.build(g)", g=holes) is None  # a profile with holes would be a guess
    flat2d = {"type": "LineString", "coordinates": [[10.0, 52.0], [10.0, 52.1]]}
    assert js("P.build(g)", g=flat2d) is None
    assert js("P.build(g)", g={"type": "LineString", "coordinates": [[10.0, 52.0, 1]]}) is None
    assert js("P.build(g)", g=route(lambda d: 5.0, vertices=5)) is None  # under 200 m
    assert js("P.build(g)", g={"type": "Point", "coordinates": [1, 2, 3]}) is None
    assert js("P.build(null)") is None
    nan = json.loads(json.dumps(good))
    nan["coordinates"][3][2] = None
    assert js("P.build(g)", g=nan) is None  # a null elevation is no elevation


def test_the_length_is_measured_along_the_route():
    got = js("P.build(g).totalM", g=long_route(lambda d: 0.0, 5))
    assert got == pytest.approx(5000, rel=0.01)  # 224 steps of 22.24 m


def test_the_parts_of_a_multilinestring_are_one_profile():
    a = long_route(lambda d: 100.0, 1)["coordinates"]
    b = [[p[0], p[1] + 0.01, p[2]] for p in a]
    multi = {"type": "MultiLineString", "coordinates": [a, b]}
    assert js("P.build(g).totalM", g=multi) > 1900


# -------------------------------------------------------------------------------- smoothing


def test_smoothing_keeps_a_constant_and_flattens_a_spike():
    cum = [i * 20.0 for i in range(21)]
    flat = [100.0] * 21
    assert js("P.smooth(c, e, 100)", c=cum, e=flat) == pytest.approx(flat)
    spiked = [100.0] * 21
    spiked[10] = 130.0
    smoothed = js("P.smooth(c, e, 100)", c=cum, e=spiked)
    assert max(smoothed) < 115 and min(smoothed) >= 100  # the 30 m jitter is spread out
    assert sum(smoothed) / 21 == pytest.approx(sum(spiked) / 21, rel=0.05)  # not a different route


def test_smoothing_copes_with_uneven_vertex_spacing():
    cum = [0, 5, 10, 400, 405, 800]
    out = js("P.smooth(c, e, 100)", c=cum, e=[10, 11, 12, 50, 51, 90])
    assert len(out) == 6 and all(math.isfinite(v) for v in out)
    assert out == sorted(out)  # a monotone climb stays monotone


# ------------------------------------------------------------------------ grade and climbs


def test_a_steady_six_percent_climb_is_one_climb_of_the_right_size():
    geometry = long_route(lambda d: 200 + 0.06 * d, 5)
    steep = js("P.steepest(P.build(g))", g=geometry)
    assert steep["grade"] == pytest.approx(0.06, abs=0.004)
    [climb] = js("P.climbs(P.build(g))", g=geometry)
    assert climb["gainM"] == pytest.approx(300, rel=0.05) and climb["lengthM"] == pytest.approx(
        5000, rel=0.03
    )
    assert climb["grade"] == pytest.approx(0.06, abs=0.004) and climb["fromM"] < 100


def test_a_descent_has_a_negative_steepest_grade_and_no_climbs():
    geometry = long_route(lambda d: 500 - 0.08 * d, 3)
    assert js("P.steepest(P.build(g)).grade", g=geometry) == pytest.approx(-0.08, abs=0.005)
    assert js("P.climbs(P.build(g))", g=geometry) == []


def test_a_flat_route_with_jitter_has_no_climbs_and_no_steep_stretch():
    jitter = [0, 1.4, -1.2, 0.9, -1.5, 1.1, -0.8, 1.3, -1.0]
    geometry = long_route(lambda d: 100 + jitter[int(d / STEP_M) % 9], 6)
    assert js("P.climbs(P.build(g))", g=geometry) == []
    assert abs(js("P.steepest(P.build(g)).grade", g=geometry)) < 0.02  # noise is not a hill


def test_a_small_dip_inside_a_climb_does_not_split_it_but_a_big_one_does():
    small = long_route(lambda d: 100 + 0.05 * d - (5 if 2000 < d < 2300 else 0), 5)
    assert len(js("P.climbs(P.build(g))", g=small)) == 1
    big = long_route(
        lambda d: (
            100 + 0.05 * d
            if d < 2500
            else 100 + 0.05 * 2500 - 0.06 * (d - 2500)
            if d < 3100
            else 100 + 0.05 * 2500 - 36 + 0.05 * (d - 3100)
        ),
        6,
    )
    found = js("P.climbs(P.build(g), 5)", g=big)
    assert len(found) == 2 and found[0]["gainM"] >= found[1]["gainM"]  # biggest first


def test_only_climbs_worth_naming_are_listed():
    short = long_route(lambda d: 100 + (0.10 * d if d < 300 else 30), 3)  # 300 m long
    assert js("P.climbs(P.build(g))", g=short) == []
    low = long_route(lambda d: 100 + 0.01 * d, 3)  # 1 %: a slope, not a climb
    assert js("P.climbs(P.build(g))", g=low) == []
    little = long_route(lambda d: 100 + 0.04 * min(d, 500), 3)  # 20 m gain
    assert js("P.climbs(P.build(g))", g=little) == []


def test_the_list_of_climbs_is_limited_and_sorted_by_gain():
    def hills(d):
        n = int(d // 1500)
        phase = d - n * 1500
        gain = [60, 150, 90, 200][n % 4]
        up = min(phase, 1000) / 1000 * gain
        return 100 + up - (phase - 1000) / 500 * gain if phase > 1000 else 100 + up

    geometry = long_route(hills, 6)
    found = js("P.climbs(P.build(g), 2)", g=geometry)
    assert len(found) == 2 and found[0]["gainM"] >= found[1]["gainM"]


# ----------------------------------------------------------------------------------- at()


def test_the_position_elevation_and_grade_at_a_distance():
    geometry = long_route(lambda d: 100 + 0.05 * d, 4)
    here = js("P.at(P.build(g), 2000)", g=geometry)
    assert here["distanceM"] == 2000 and here["elevationM"] == pytest.approx(200, abs=3)
    assert here["grade"] == pytest.approx(0.05, abs=0.004)
    assert here["lon"] == pytest.approx(10.0) and here["lat"] == pytest.approx(
        52.0 + 2000 / 111195, abs=1e-4
    )


def test_a_distance_outside_the_route_is_clamped_to_its_ends():
    geometry = long_route(lambda d: 100 + 0.05 * d, 2)
    assert js("P.at(P.build(g), -50).distanceM", g=geometry) == 0
    total = js("P.build(g).totalM", g=geometry)
    assert js("P.at(P.build(g), 99999).distanceM", g=geometry) == total
    assert js("P.distanceAtFraction(P.build(g), 1.5)", g=geometry) == total
    assert js("P.distanceAtFraction(P.build(g), -1)", g=geometry) == 0


# ------------------------------------------------------------------------ bands and ticks


@pytest.mark.parametrize(
    ("percent", "label"),
    [(-12, "steep descent"), (-8, "descent"), (-3.1, "descent"), (-3, "flat"), (0, "flat"),
     (2.99, "flat"), (3, "climb"), (5.9, "climb"), (6, "steep climb"), (10, "very steep climb"),
     (25, "very steep climb")],
)  # fmt: skip
def test_the_grade_bands(percent, label):
    assert js("P.bandOf(x / 100).label", x=percent) == label


@pytest.mark.parametrize(
    ("km", "step"), [(3, 1), (7, 2), (12, 2), (25, 5), (34, 10), (60, 10), (150, 50), (900, 200)]
)
def test_the_ticks_are_round_numbers_and_few(km, step):
    got = js("P.tickStepKm(x, 6)", x=km)
    assert got >= step * 0.5 and km / got <= 6


# -------------------------------------------------------------------------------- the chart


def test_the_chart_is_an_accessible_svg_with_the_range_and_the_length():
    svg = js("P.chartSvg(P.build(g))", g=long_route(lambda d: 200 + 0.05 * d, 4))
    assert svg.startswith("<svg") and svg.endswith("</svg>") and 'role="img"' in svg
    assert 'viewBox="0 0 340 150"' in svg
    assert re.search(r'aria-label="Elevation profile: 4 km, from 2\d\d m to [34]\d\d m"', svg)
    assert svg.count("<polygon") > 100 and '<path class="profile-line"' in svg
    assert "NaN" not in svg and "undefined" not in svg and "Infinity" not in svg
    assert 'data-left="38"' in svg and 'data-right="332"' in svg and 'data-width="340"' in svg
    assert ">0 km<" in svg  # the first tick says the unit


def test_the_colour_follows_the_grade():
    climbing = js("P.chartSvg(P.build(g))", g=long_route(lambda d: 100 + 0.08 * d, 3))
    assert "#e8892b" in climbing and "#8aa39b" not in climbing  # steep climb, no flat
    flat = js("P.chartSvg(P.build(g))", g=long_route(lambda d: 100.0, 3))
    assert "#8aa39b" in flat and "#e8892b" not in flat
    down = js("P.chartSvg(P.build(g))", g=long_route(lambda d: 300 - 0.10 * d, 3))
    assert "#1d4e89" in down


def test_a_flat_route_is_not_stretched_into_a_mountain():
    # 8 m of jitter on a flat ride: the axis still spans at least 40 m, so the line stays flat
    svg = js(
        "P.chartSvg(P.build(g))", g=long_route(lambda d: 100 + (4 if int(d / 100) % 2 else 0), 3)
    )
    ys = [float(y) for y in re.findall(r"L[\d.]+,([\d.]+)", svg)]
    assert max(ys) - min(ys) < (150 - 28) * 0.3  # under a third of the plot height


def test_the_chart_options_change_the_size():
    svg = js(
        "P.chartSvg(P.build(g), {width: 300, height: 100})",
        g=long_route(lambda d: 100 + 0.05 * d, 3),
    )
    assert 'viewBox="0 0 300 100"' in svg and 'data-width="300"' in svg


# ------------------------------------------------------------------------- the page wiring


def test_the_page_shows_the_card_draws_the_chart_and_links_the_cursor_to_the_map():
    html = (FRONTEND / "index.html").read_text()
    assert 'id="profile-card"' in html and 'id="profile-chart"' in html
    assert (
        html.index('src="status.js"') < html.index('src="profile.js"') < html.index('src="app.js"')
    )
    app = (FRONTEND / "app.js").read_text()
    render = app[app.index("function renderProfile") :]
    render = render[: render.index("\nfunction updateActiveView")]
    assert (
        "BikeProfile.build(cand.geometry_geojson)" in render
        and "els.profileCard.hidden = true" in render
    )
    assert "pointermove" in render and "pointerleave" in render and "ArrowRight" in render
    assert 'setAttribute("tabindex", "0")' in render  # reachable with the keyboard
    show = app[app.index("function showProfileCursor") : app.index("function renderProfile")]
    assert "L.circleMarker" in show and "setLatLng" in show and "BikeProfile.at(" in show
    # the card follows the route that is shown, and goes away with the results
    assert "renderProfile(cand);" in app[app.index("function updateActiveView") :]
    cleared = app[app.index("function clearResults") :]
    assert "clearProfileCursor();" in cleared[: cleared.index("\n}\n")]


# --------------------------------------------------------------- colouring the route on the map


def hill(d):
    """Flat for 1 km, then a 8 % climb for 1 km, then flat again."""
    if d < 1000:
        return 100.0
    if d < 2000:
        return 100.0 + (d - 1000) * 0.08
    return 180.0


def pieces_of(geometry):
    return js("P.bandPieces(P.build(g))", g=geometry)


def test_the_route_is_cut_into_runs_of_one_grade_band():
    pieces = pieces_of(long_route(hill, 3))
    assert [p["key"] for p in pieces] == ["flat", "climb", "steep climb", "climb", "flat"]
    # the climb is coloured by the grade over 300 m around each point, so the edges blend
    assert pieces[0]["fromM"] == 0 and pieces[-1]["toM"] == pytest.approx(3000, rel=0.01)
    assert all(a["toM"] == b["fromM"] for a, b in zip(pieces, pieces[1:], strict=False))
    steep = next(p for p in pieces if p["key"] == "steep climb")
    assert steep["fromM"] > 1000 and steep["toM"] < 2000  # the 8 % section is inside the hill


def test_a_piece_ends_exactly_where_the_next_begins_on_the_map():
    pieces = pieces_of(long_route(hill, 3))
    for a, b in zip(pieces, pieces[1:], strict=False):
        assert a["latlngs"][-1] == b["latlngs"][0]  # no gap between the coloured lines
    assert pieces[0]["latlngs"][0] == [52.0, 10.0]  # latitude first, as the map wants it


def test_the_pieces_hold_the_vertices_between_their_ends():
    pieces = pieces_of(long_route(hill, 3))
    inside = sum(len(p["latlngs"]) - 2 for p in pieces)
    assert inside == pytest.approx(3000 / STEP_M, abs=len(pieces) + 2)  # every vertex once


def test_a_steady_descent_is_one_piece():
    pieces = pieces_of(long_route(lambda d: 200.0 - d * 0.05, 2))
    assert [p["key"] for p in pieces] == ["descent"]


def test_the_band_legend_adds_up_and_leaves_out_tiny_bands():
    pieces = pieces_of(long_route(hill, 3))
    legend = js("P.bandLegend(p)", p=pieces)
    assert [r["key"] for r in legend] == ["flat", "climb", "steep climb"]  # steepest descent first
    assert sum(r["share"] for r in legend) == pytest.approx(1.0)
    assert legend[0]["label"] == "Flat" and legend[2]["label"] == "Steep climb"
    flat = next(r for r in legend if r["key"] == "flat")
    assert flat["meters"] == pytest.approx(2000, rel=0.15)
    tiny = [{"key": "flat", "toM": 10, "fromM": 0}, {"key": "climb", "fromM": 10, "toM": 5000}]
    assert [r["key"] for r in js("P.bandLegend(p)", p=tiny)] == ["climb"]
    assert js("P.bandLegend(null)") == [] and js("P.bandLegend([])") == []


def test_the_colours_are_the_charts_colours():
    pieces = pieces_of(long_route(hill, 3))
    bands = {b["label"]: b["color"] for b in js("P.GRADE_BANDS")}
    assert all(p["color"] == bands[p["key"]] for p in pieces)


def test_the_page_offers_gradient_where_there_is_a_profile_and_remembers_it():
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert '...(profile ? ["gradient"] : [])' in app
    assert '["surface", "traffic", "gradient"].includes(saved)' in app
    assert "BikeProfile.bandPieces(profile)" in app
