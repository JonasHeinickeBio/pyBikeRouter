"""The placement logic for a point picked on the map (frontend/waypoints.js), run under node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "src" / "bike_routing_agent" / "frontend"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def call(name: str, *args):
    script = (
        f"const W = require({json.dumps(str(FRONTEND / 'waypoints.js'))});\n"
        f"process.stdout.write(JSON.stringify(W.{name}("
        f"{', '.join(json.dumps(a) for a in args)})));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def keys(options):
    return [o["key"] for o in options]


# ---------------------------------------------------------------------------------- text


@pytest.mark.parametrize(
    "lon, expected",
    [(10, 10), (190, -170), (-200, 160), (360, 0), (-180, -180), (180, -180), (539.5, 179.5)],
)
def test_a_longitude_from_a_panned_map_is_wrapped_into_range(lon, expected):
    assert call("wrapLon", lon) == pytest.approx(expected)


def test_a_point_becomes_the_lat_lon_text_the_form_reads():
    assert call("coordText", 52.26899, 10.52701) == "52.26899, 10.52701"
    assert call("coordText", 52.2, 10.5) == "52.20000, 10.50000"
    assert call("coordText", 95, 190) == "90.00000, -170.00000"  # a click past the pole or edge


# --------------------------------------------------------------------------------- slots


def test_a_new_stop_fits_at_every_position_between_start_and_end():
    options = call("slots", 2)
    assert keys(options) == ["start", "via:0", "via:1", "via:2", "end"]
    labels = [o["label"] for o in options]
    assert labels[1] == "Stop 1 – after the start"
    assert labels[2] == "Stop 2 – after stop 1"
    assert labels[3] == "Stop 3 – after stop 2, before the end"
    assert "replaces" in labels[0] and "replaces" in labels[4]


def test_with_no_stops_yet_there_is_one_position_between_start_and_end():
    options = call("slots", 0)
    assert keys(options) == ["start", "via:0", "end"]
    assert options[1]["label"] == "Stop 1 – after the start, before the end"


def test_empty_start_and_end_are_not_described_as_replacing_anything():
    options = call("slots", 0, {"hasOrigin": False, "hasDestination": False})
    assert options[0]["label"] == "Start (A)" and options[-1]["label"] == "End (B)"
    assert options[1]["label"] == "Stop 1 – after the start"  # no end to be before


def test_no_stop_is_offered_once_the_trip_is_full():
    assert keys(call("slots", 9))[1:-1] == [f"via:{k}" for k in range(10)]
    assert keys(call("slots", 10)) == ["start", "end"]  # the server takes at most 10


# ------------------------------------------------------------------------------- suggest

A = {"lat": 52.0, "lon": 10.0}
B = {"lat": 52.0, "lon": 11.0}
V1 = {"lat": 52.0, "lon": 10.4}
V2 = {"lat": 52.0, "lon": 10.7}
base = {
    "hasOrigin": True,
    "hasDestination": True,
    "origin": A,
    "destination": B,
    "vias": [],
    "route": None,
}


def suggest(point, **over):
    return call("suggest", {**base, "point": point, **over})


def test_the_first_point_is_the_start_and_the_next_one_the_end():
    point = {"lat": 52.1, "lon": 10.5}
    assert (
        suggest(point, hasOrigin=False, origin=None, hasDestination=False, destination=None)
        == "start"
    )
    assert suggest(point, hasDestination=False, destination=None) == "end"
    assert suggest(point, hasOrigin=False, origin=None) == "start"  # an end but no start


def test_between_start_and_end_with_no_stops_it_is_the_first_stop():
    assert suggest({"lat": 52.05, "lon": 10.5}) == "via:0"


def test_the_stop_goes_where_it_lies_along_the_straight_lines_between_the_places():
    vias = [V1, V2]
    assert suggest({"lat": 52.01, "lon": 10.2}, vias=vias) == "via:0"  # between A and stop 1
    assert suggest({"lat": 52.01, "lon": 10.55}, vias=vias) == "via:1"  # between stops 1 and 2
    assert suggest({"lat": 52.01, "lon": 10.9}, vias=vias) == "via:2"  # between stop 2 and B


def test_with_a_route_on_the_map_the_position_along_that_route_counts():
    # A route that detours far south before it reaches stop 1: a point on the detour is still
    # *before* the stop, although the straight line from A would put it elsewhere.
    route = [[10.0, 52.0], [10.1, 51.5], [10.3, 51.5], [10.4, 52.0], [10.7, 52.0], [11.0, 52.0]]
    detour_point = {"lat": 51.5, "lon": 10.2}
    assert suggest(detour_point, vias=[V1, V2], route=route) == "via:0"
    assert suggest({"lat": 52.0, "lon": 10.55}, vias=[V1, V2], route=route) == "via:1"


def test_a_stop_that_is_only_a_name_cannot_be_placed_so_the_new_one_goes_last():
    assert suggest({"lat": 52.01, "lon": 10.2}, vias=[None, V2]) == "via:2"
    assert suggest({"lat": 52.01, "lon": 10.2}, origin=None) == "via:0"  # no line at all: ...
    # ... a start that is a name leaves nothing to measure along: with no stops it is the first
    assert suggest({"lat": 52.01, "lon": 10.2}, vias=[V1], destination=None) == "via:1"


def test_a_full_trip_suggests_the_end_because_no_stop_fits():
    vias = [V1] * 10
    assert suggest({"lat": 52.05, "lon": 10.5}, vias=vias) == "end"


# --------------------------------------------------------------------------------- place


def places(vias=("a", "b")):
    return {"origin": "Start", "destination": "End", "vias": list(vias)}


def test_a_stop_is_inserted_at_its_position_and_numbered_from_there():
    got = call("place", places(), "via:1", "52.1, 10.1")
    assert got == {
        "origin": "Start",
        "destination": "End",
        "vias": ["a", "52.1, 10.1", "b"],
        "label": "stop 2",
    }
    assert call("place", places(), "via:0", "x")["vias"] == ["x", "a", "b"]
    assert call("place", places(), "via:2", "x")["vias"] == ["a", "b", "x"]
    assert call("place", places(), "via:2", "x")["label"] == "stop 3"


def test_the_start_and_the_end_are_replaced_and_the_stops_stay():
    start = call("place", places(), "start", "52.1, 10.1")
    assert start["origin"] == "52.1, 10.1" and start["destination"] == "End"
    assert start["vias"] == ["a", "b"] and start["label"] == "the start (A)"
    end = call("place", places(), "end", "52.1, 10.1")
    assert end["destination"] == "52.1, 10.1" and end["origin"] == "Start"
    assert end["label"] == "the end (B)"


def test_a_position_past_the_last_stop_is_clamped_to_the_end_of_the_list():
    assert call("place", places(), "via:7", "x")["vias"] == ["a", "b", "x"]


def test_a_bad_key_or_a_full_trip_places_nothing():
    assert call("place", places(), "middle", "x") is None
    assert call("place", places(), "via:", "x") is None
    assert call("place", places(["s"] * 10), "via:3", "x") is None


def test_the_places_given_are_not_changed():
    original = places()
    script = (
        f"const W = require({json.dumps(str(FRONTEND / 'waypoints.js'))});\n"
        f"const p = {json.dumps(original)}; W.place(p, 'via:0', 'x');\n"
        "process.stdout.write(JSON.stringify(p));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert json.loads(result.stdout) == original


# ------------------------------------------------------------------------- the page wiring


def test_the_page_loads_the_module_and_the_map_click_opens_the_menu():
    html = (FRONTEND / "index.html").read_text()
    assert (
        html.index('src="pois.js"') < html.index('src="waypoints.js"') < html.index('src="app.js"')
    )
    app = (FRONTEND / "app.js").read_text()
    click = app[app.index("function onMapClick") :]
    click = click[: click.index("\n}\n")]
    assert "openPointMenu(ev.latlng)" in click
    assert "pickingTarget" in click  # picking for a field still has priority
    # a click that only closes another popup must not open the menu
    assert "foreignPopupClosedAt" in app and 'className !== "point-popup"' in app
    # the menu, both ways of adding, and the position-aware insertion of a stop
    assert "BikeWaypoints.slots(" in app and "BikeWaypoints.suggest(" in app
    assert "BikeWaypoints.place(" in app and "insertViaRow(" in app
    assert "latlng.wrap()" in app  # a map panned across the date line reports a longitude > 180
