"""frontend/link.js: a link that opens the planner with a trip in it, under node."""

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "src" / "bike_routing_agent" / "frontend"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

NOW = 1_800_000_000_000  # a fixed "now" in ms (2027-01-15)
HOUR = 3600 * 1000


def run_js(expression: str):
    script = (
        f"const L = require({json.dumps(str(FRONTEND / 'link.js'))});\n"
        f"process.stdout.write(JSON.stringify({expression}));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def encode(request, now=NOW):
    return run_js(f"L.encode({json.dumps(request)}, {now})")


def decode(fragment, now=NOW):
    return run_js(f"L.decode({json.dumps(fragment)}, {now})")


def iso(offset_ms):
    from datetime import UTC, datetime

    return (
        datetime.fromtimestamp((NOW + offset_ms) / 1000, tz=UTC)
        .isoformat()
        .replace("+00:00", ".000Z")
    )


def fragment_of(payload: str) -> str:
    raw = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"#plan={raw}"


TRIP = {
    "origin": "Hannover",
    "destination": {"lat": 52.15, "lon": 9.95},
    "via": ["Laatzen"],
    "constraints": {
        "bike_type": "gravel",
        "target_distance_km": 60,
        "max_distance_km": None,
        "max_ascent_m": 500,
        "prefer_surfaces": ["gravel", "asphalt"],
        "avoid_surfaces": [],
        "avoid_high_traffic_roads": True,
        "avoid_ferries": True,
        "return_to_origin": False,
    },
    "max_alternatives": 3,
}


def test_a_trip_round_trips_through_a_link():
    fragment = encode(TRIP)
    assert fragment.startswith("#plan=") and "=" not in fragment[6:]  # no padding in a URL
    got = decode(fragment)
    assert got["origin"] == "Hannover" and got["destination"] == {"lat": 52.15, "lon": 9.95}
    assert got["via"] == ["Laatzen"] and got["max_alternatives"] == 3
    assert got["constraints"]["bike_type"] == "gravel"
    assert got["constraints"]["prefer_surfaces"] == ["gravel", "asphalt"]
    assert got["constraints"]["avoid_high_traffic_roads"] is True
    assert "max_distance_km" not in got["constraints"]  # a null is simply absent


def test_places_with_unicode_survive():
    fragment = encode({**TRIP, "origin": "Münster (Westf.) – Hbf", "destination": "Göttingen"})
    assert decode(fragment)["origin"] == "Münster (Westf.) – Hbf"


def test_the_fragment_holds_no_readable_place_name():
    # not a secret, but the point of the fragment is that it is not part of any request
    assert encode(TRIP).startswith("#")


def test_a_trip_without_two_places_is_no_link():
    assert encode({**TRIP, "origin": ""}) is None
    assert encode({**TRIP, "destination": "   "}) is None
    assert encode({"via": []}) is None
    assert encode(None) is None


def test_a_departure_in_the_future_is_kept_and_a_past_one_is_dropped():
    future = {**TRIP, "departure_time": iso(2 * HOUR)}
    assert decode(encode(future))["departure_time"] == iso(2 * HOUR)
    assert "departure_time" not in decode(encode({**TRIP, "departure_time": iso(-HOUR)}))
    assert "departure_time" not in decode(encode({**TRIP, "departure_time": iso(15 * 24 * HOUR)}))
    # a link made earlier is read later: the same text, an hour past its departure
    link = encode(future)
    assert "departure_time" not in decode(link, NOW + 3 * HOUR)


def test_a_departure_that_is_not_a_date_is_dropped():
    assert "departure_time" not in decode(encode({**TRIP, "departure_time": "tomorrow"}))


def test_only_the_known_constraints_get_through():
    hostile = {
        **TRIP,
        "constraints": {
            "bike_type": "tank",
            "max_ascent_m": -5,
            "target_distance_km": "far",
            "return_to_origin": "yes",
            "avoid_ferries": False,
            "prefer_surfaces": ["ok", 5, "", "x" * 100],
            "evil": "<script>",
        },
    }
    got = decode(encode(hostile))
    assert got["constraints"] == {"avoid_ferries": False, "prefer_surfaces": ["ok"]}


def test_places_are_checked_and_limited():
    many = {**TRIP, "via": [f"p{i}" for i in range(30)]}
    assert len(decode(encode(many))["via"]) == 10
    odd = {**TRIP, "via": [None, 5, {"lat": 95, "lon": 0}, {"lat": 1}, ["a"], "ok", "x" * 300]}
    assert decode(encode(odd))["via"] == ["ok"]
    assert encode({**TRIP, "origin": {"lat": 91, "lon": 0}}) is None
    assert encode({**TRIP, "origin": "x" * 300}) is None


def test_alternatives_must_be_one_to_five_integers():
    assert decode(encode({**TRIP, "max_alternatives": 9})).get("max_alternatives") is None
    assert decode(encode({**TRIP, "max_alternatives": 2.5})).get("max_alternatives") is None
    assert decode(encode({**TRIP, "max_alternatives": 5}))["max_alternatives"] == 5


def test_a_trip_too_big_for_a_link_is_none():
    huge = {**TRIP, "via": ["y" * 200] * 10}  # as many long places as a link may hold
    assert encode(huge) is None
    assert encode({**TRIP, "via": ["y" * 100] * 5}) is not None


@pytest.mark.parametrize(
    "fragment",
    [
        None,
        "",
        "#",
        "#plan=",
        "#plan=!!!",
        "#other=abc",
        "plan=abc",
        "#plan=" + "A" * 3500,  # too long
        fragment_of("not json"),
        fragment_of("[1, 2]"),
        fragment_of('"text"'),
        fragment_of('{"origin": "A"}'),  # no destination
        fragment_of('{"origin": 5, "destination": 6}'),
        "#plan=/w",  # a character outside base64url
        "#plan=_w",  # base64url for the byte 0xFF: not valid UTF-8
    ],
)
def test_anything_that_is_not_a_usable_trip_is_ignored(fragment):
    assert decode(fragment) is None


def test_a_hand_written_link_works_too():
    link = fragment_of(json.dumps({"origin": "Oxford", "destination": "Cambridge"}))
    assert decode(link) == {
        "origin": "Oxford",
        "destination": "Cambridge",
        "via": [],
        "constraints": {},
    }


# ----------------------------------------------------------------------------- page wiring


def test_the_page_has_the_button_and_loads_the_link_before_the_chat_restores():
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert 'id="copy-link-btn"' in page
    assert page.index('src="link.js"') < page.index('src="app.js"')
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert app.index("  loadSharedPlan();") < app.index("  initChat();")
    assert "state.planPending === null" in app  # a restored chat does not draw over the link's trip
    assert (
        'window.addEventListener("hashchange", loadSharedPlan)' in app
    )  # a link pasted into an open tab
    assert "state.engineOverride = keep;" in app  # copying a link is not a plan
