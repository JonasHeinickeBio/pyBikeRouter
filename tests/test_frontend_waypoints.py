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


# ------------------------------------------------------------------------ dragging the route

EAST = [[10.0 + i * 0.1, 52.0] for i in range(11)]  # a route from (10, 52) east to (11, 52)
TRIP = {
    "hasOrigin": True,
    "hasDestination": True,
    "origin": {"lat": 52.0, "lon": 10.0},
    "destination": {"lat": 52.0, "lon": 11.0},
    "vias": [],
    "route": EAST,
}


def reshape(**changes):
    return call("reshape", {**TRIP, **changes})


def test_dragging_a_route_adds_a_stop_at_the_drop_point_in_the_right_place():
    got = reshape(grab={"lat": 52.0, "lon": 10.5}, drop={"lat": 52.05, "lon": 10.5})
    assert got == {"key": "via:0", "text": "52.05000, 10.50000"}


def test_the_stop_goes_where_the_route_was_grabbed_not_where_it_was_dropped():
    one = [{"lat": 52.0, "lon": 10.5}]
    # grabbed before the existing stop, dropped far beyond it: still the first stop
    early = reshape(vias=one, grab={"lat": 52.0, "lon": 10.2}, drop={"lat": 52.1, "lon": 10.9})
    assert early["key"] == "via:0"
    late = reshape(vias=one, grab={"lat": 52.0, "lon": 10.8}, drop={"lat": 52.1, "lon": 10.1})
    assert late["key"] == "via:1"


def test_dragging_with_a_stop_that_is_only_a_name_puts_the_new_one_last():
    got = reshape(vias=[None], grab={"lat": 52.0, "lon": 10.2}, drop={"lat": 52.1, "lon": 10.3})
    assert got["key"] == "via:1"


@pytest.mark.parametrize(
    "changes",
    [
        {"hasOrigin": False},
        {"hasDestination": False},
        {"grab": None},
        {"drop": None},
        {"vias": [{"lat": 52.0, "lon": 10.1 + i * 0.05} for i in range(10)]},  # no room: 10 stops
    ],
)
def test_nothing_to_add_without_a_trip_a_grab_a_drop_or_room(changes):
    base = {"grab": {"lat": 52.0, "lon": 10.5}, "drop": {"lat": 52.1, "lon": 10.5}}
    assert reshape(**{**base, **changes}) is None


def test_a_drop_past_the_antimeridian_is_written_wrapped():
    got = reshape(grab={"lat": 52.0, "lon": 10.5}, drop={"lat": 52.0, "lon": 190.5})
    assert got["text"] == "52.00000, -169.50000"


def test_the_page_wires_the_drag_handle_to_the_route_lines_and_clears_it_with_the_results():
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "attachReshape(L.geoJSON(candidate.geometry_geojson" in app
    assert (
        "BikeWaypoints.reshape({" in app
        and "applyPoint(placement.key, placement.text, true)" in app
    )
    assert "RESHAPE_MIN_DRAG_PX" in app  # a slip of the pointer is not a new stop
    # the handle sits under the pointer on the route: a plain click must still reach the map menu
    assert "bubblingMouseEvents: true" in app[app.index("function showReshapeHandle") :]
    # the handle only follows the active route, never while a point is being picked
    assert "index !== state.activeIndex || state.pickingTarget" in app
    assert "hideReshapeHandle();" in app[app.index("function clearResults") :]


def test_the_place_markers_can_be_dragged_and_a_drop_moves_that_place_and_plans_again():
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    markers = app[app.index("function redrawPlaceMarkers") : app.index("function movePlace")]
    assert "draggable: true" in markers and 'marker.on("dragend"' in markers
    # each marker knows its own field: A the origin, 1..n the stops in order, B the destination
    assert "input: els.origin" in markers and "input: els.destination" in markers
    assert 'idx + 1), cls: "", input }' in markers
    move = app[app.index("function movePlace") :]
    assert "BikeWaypoints.coordText(ll.lat, ll.lng)" in move  # a wrapped, 5-decimal coordinate
    assert "latlng.wrap()" in move and "planRoute();" in move


def test_the_click_that_ends_a_drag_is_swallowed_for_both_kinds_of_drag():
    """A drop over a map control makes the browser click the map: that must not open the menu."""
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    click = app[app.index("function onMapClick") :]
    assert click.index("performance.now() < state.ignoreMapClickUntil") < click.index(
        "openPointMenu"
    )
    assert "ignoreMapClickUntil: 0" in app  # starts open: a first plain click still works
    guard = app[app.index("function guardClickAfterDrag") :]
    assert "performance.now() + DRAG_CLICK_GUARD_MS" in guard
    # the guard is set by the place markers' drag and by the route handle's drag
    assert app[app.index("function redrawPlaceMarkers") :].count("guardClickAfterDrag();") >= 1
    assert app[app.index("function showReshapeHandle") :].count("guardClickAfterDrag();") >= 1


def test_a_long_press_places_the_drag_handle_and_a_tap_away_drops_it():
    """A touch screen cannot hover, so the handle comes from a long press (a `contextmenu`)."""
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert (
        "tapHold: true" in app
    )  # Leaflet sends the long press as contextmenu on every touch browser
    # a DOM listener, never a Leaflet one: Leaflet cancels the browser menu of every contextmenu
    # it passes to a listener, which would take a mouse's right click away (found in review)
    assert 'layer.on("contextmenu"' not in app and 'map.on("contextmenu"' not in app
    assert 'getContainer().addEventListener("contextmenu", onLongPress)' in app
    press = app[app.index("function onLongPress") : app.index("function attachReshape")]
    assert 'ev._simulated || (ev.pointerType && ev.pointerType !== "mouse")' in press
    assert "state.pickingTarget" in press and "BikeWaypoints.nearestOnRoute(" in press
    assert "state.reshapeSticky = true" in press
    # sticky: no hide timer while it is set; a tap elsewhere on the map clears it
    assert "state.reshapeDragging || state.reshapeSticky" in app
    click = app[app.index("function onMapClick") :]
    assert click.index("state.reshapeSticky") < click.index("openPointMenu")
    dismiss = click[click.index("if (state.reshapeSticky)") :].split("}")[0]
    assert "hideReshapeHandle();" in dismiss and "return;" in dismiss  # it only dismisses
    # a handle belongs to its route and must not outlive the picker or the route change (review)
    assert "if (index !== state.activeIndex) hideReshapeHandle();" in app
    assert "hideReshapeHandle(); // the first tap of a picker must pick" in app
    # and the page tells a touch user how
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert 'id="reshape-hint"' in page and "press and hold the route" in page
    assert '"ontouchstart" in window || navigator.maxTouchPoints > 0' in app


# a 1 pixel = 1 degree plane: the projection of the tests
IDENT = "(lon, lat) => ({x: lon, y: lat})"


def nearest(route, target, reach):
    script = (
        f"const W = require({json.dumps(str(FRONTEND / 'waypoints.js'))});\n"
        f"process.stdout.write(JSON.stringify(W.nearestOnRoute({json.dumps(route)}, "
        f"{json.dumps(target)}, {IDENT}, {reach})));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


L_ROUTE = [[0, 0], [100, 0], [100, 100]]  # east, then north


def test_a_long_press_snaps_to_the_nearest_point_of_the_route():
    got = nearest(L_ROUTE, {"x": 40, "y": 20}, 28)
    assert (got["lon"], got["lat"], got["distance"]) == (40, 0, 20)  # beside the first leg
    got = nearest(L_ROUTE, {"x": 120, "y": 60}, 28)
    assert (got["lon"], got["lat"], got["distance"]) == (100, 60, 20)  # beside the second leg


def test_a_press_in_the_corner_picks_the_nearest_of_the_two_legs_and_clamps_to_the_ends():
    got = nearest(L_ROUTE, {"x": 110, "y": -10}, 28)
    assert (got["lon"], got["lat"]) == (100, 0)  # the corner itself: the nearest point
    got = nearest(L_ROUTE, {"x": -15, "y": 0}, 28)
    assert (got["lon"], got["lat"]) == (0, 0)  # before the start: clamped to the first vertex


def test_a_press_too_far_from_the_route_is_not_a_press_on_it():
    assert nearest(L_ROUTE, {"x": 50, "y": 29}, 28) is None
    assert nearest(L_ROUTE, {"x": 50, "y": 28}, 28) is not None  # exactly the reach is still on it


@pytest.mark.parametrize("route", [None, [], [[0, 0]], "nope"])
def test_nothing_to_snap_to_without_a_route(route):
    assert nearest(route, {"x": 0, "y": 0}, 28) is None


def test_repeated_vertices_do_not_break_the_snap():
    got = nearest([[0, 0], [0, 0], [50, 0]], {"x": 25, "y": 5}, 28)
    assert (got["lon"], got["lat"], got["distance"]) == (25, 0, 5)


# ------------------------------------------------------------- the preview line while dragging


def neighbours(**changes):
    return call("reshapeNeighbours", {**TRIP, **changes})


def test_the_preview_runs_between_the_places_the_new_stop_will_sit_between():
    got = neighbours(grab={"lat": 52.0, "lon": 10.5})
    assert got == {"before": [52.0, 10.0], "after": [52.0, 11.0]}  # origin and destination


def test_with_stops_it_is_the_ones_either_side_of_where_the_route_was_grabbed():
    vias = [{"lat": 52.0, "lon": 10.3}, {"lat": 52.0, "lon": 10.7}]
    middle = neighbours(vias=vias, grab={"lat": 52.0, "lon": 10.5})
    assert middle == {"before": [52.0, 10.3], "after": [52.0, 10.7]}
    first = neighbours(vias=vias, grab={"lat": 52.0, "lon": 10.1})
    assert first == {"before": [52.0, 10.0], "after": [52.0, 10.3]}
    last = neighbours(vias=vias, grab={"lat": 52.0, "lon": 10.9})
    assert last == {"before": [52.0, 10.7], "after": [52.0, 11.0]}


def test_a_neighbour_that_is_only_a_name_has_no_position_and_is_null():
    got = neighbours(
        vias=[None], grab={"lat": 52.0, "lon": 10.5}
    )  # name-only stop: new one goes last
    assert got == {"before": None, "after": [52.0, 11.0]}


@pytest.mark.parametrize(
    "changes",
    [
        {"hasOrigin": False},
        {"hasDestination": False},
        {"grab": None},
        {"vias": [{"lat": 52.0, "lon": 10.1 + i * 0.05} for i in range(10)]},  # no room
    ],
)
def test_no_preview_without_a_trip_a_grab_or_room(changes):
    assert neighbours(**{"grab": {"lat": 52.0, "lon": 10.5}, **changes}) is None


def test_the_page_draws_and_clears_the_preview_with_the_drag():
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    handle = app[app.index("function showReshapeHandle") : app.index("function reshapeThrough")]
    assert "BikeWaypoints.reshapeNeighbours({" in handle
    assert 'marker.on("drag", ' in handle and "preview.setLatLngs(" in handle
    assert "interactive: false" in handle and 'dashArray: "6 8"' in handle
    # it lives in the handle's layer, so dropping or hiding the handle removes it
    assert ".addTo(state.reshapeLayer)" in handle
