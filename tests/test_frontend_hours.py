"""frontend/hours.js: reading OSM opening hours against the time you pass a sight, under node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "src" / "bike_routing_agent" / "frontend"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

MO, TU, WE, TH, FR, SA, SU = range(7)


def run_js(expression: str):
    script = (
        f"const H = require({json.dumps(str(FRONTEND / 'hours.js'))});\n"
        f"process.stdout.write(JSON.stringify({expression}));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def at(text: str, day: int, hhmm: str) -> dict:
    h, m = hhmm.split(":")
    return run_js(f"H.evaluate({json.dumps(text)}, {day}, {int(h) * 60 + int(m)})")


def state(text: str, day: int, hhmm: str) -> str:
    return at(text, day, hhmm)["state"]


# ----------------------------------------------------------------------------- reading the hours


def test_a_simple_weekday_rule():
    text = "Mo-Fr 09:00-18:00"
    assert state(text, WE, "12:00") == "open"
    assert state(text, WE, "08:59") == "closed"
    assert state(text, WE, "18:00") == "closed"  # the end is not inside
    assert state(text, WE, "09:00") == "open"
    assert state(text, SA, "12:00") == "closed"  # a day nobody named is closed


def test_the_verdict_names_when_it_closes_and_when_it_opens():
    assert at("Mo-Fr 09:00-18:00", WE, "12:00")["until"] == 18 * 60
    closed = at("Mo-Fr 09:00-12:00,14:00-18:00", WE, "12:30")
    assert closed["state"] == "closed" and closed["opensAt"] == 14 * 60
    assert at("Mo-Fr 09:00-18:00", WE, "19:00")["opensAt"] is None  # nothing later today


def test_several_stretches_in_one_day():
    text = "Tu-Su 10:00-12:30, 14:00-17:00"
    assert [state(text, TU, t) for t in ("10:00", "12:29", "13:00", "14:00", "17:00")] == [
        "open",
        "open",
        "closed",
        "open",
        "closed",
    ]
    assert state(text, MO, "11:00") == "closed"


def test_a_later_rule_replaces_the_earlier_ones_for_the_days_it_names():
    text = "Mo-Su 10:00-20:00; Sa 10:00-14:00; Su off"
    assert state(text, FR, "19:00") == "open"
    assert state(text, SA, "19:00") == "closed" and state(text, SA, "13:00") == "open"
    assert state(text, SU, "12:00") == "closed"


def test_day_lists_and_ranges_that_wrap_the_week():
    assert [state("Mo,We,Fr 08:00-12:00", d, "09:00") for d in (MO, TU, WE, TH, FR)] == [
        "open",
        "closed",
        "open",
        "closed",
        "open",
    ]
    assert [state("Fr-Mo 08:00-12:00", d, "09:00") for d in (TH, FR, SA, SU, MO, TU)] == [
        "closed",
        "open",
        "open",
        "open",
        "open",
        "closed",
    ]


def test_without_days_every_day_counts():
    assert [state("08:00-12:00", d, "09:00") for d in range(7)] == ["open"] * 7


def test_around_the_clock_and_midnight():
    assert at("24/7", WE, "03:00") == {"state": "open", "until": None, "holidays": False}
    assert at("Mo-Su 00:00-24:00", SU, "23:59")["until"] is None  # goes on into the next day
    assert at("Mo-Fr 00:00-24:00", FR, "23:00")["until"] == 1440  # but not into the weekend
    assert state("Mo-Fr 09:00-24:00", MO, "23:30") == "open"


def test_hours_that_run_past_midnight_open_the_next_morning_too():
    text = "Fr,Sa 20:00-02:00"
    assert state(text, FR, "23:00") == "open"
    assert at(text, SA, "01:00") == {"state": "open", "until": 120, "holidays": False}
    assert state(text, SA, "03:00") == "closed"
    assert state(text, SU, "01:00") == "open"  # the Saturday night
    assert state(text, FR, "01:00") == "closed"  # the Thursday night was not opened


def test_off_and_closed_and_open_words():
    assert state("Mo-Su 10:00-18:00; We off", WE, "12:00") == "closed"
    assert state("Mo-Su 10:00-18:00; We closed", WE, "12:00") == "closed"
    assert state("Mo-Fr open", TU, "03:00") == "open"
    assert state("off", TU, "12:00") == "closed"


def test_holidays_are_flagged_not_guessed():
    open_ = at("Mo-Sa 09:00-18:00; PH off", TU, "10:00")
    assert open_["state"] == "open" and open_["holidays"] is True
    assert at("Mo-Sa 09:00-18:00", TU, "10:00")["holidays"] is False


@pytest.mark.parametrize(
    "text",
    [
        "Apr-Oct Mo-Su 09:00-18:00",  # months
        "Mo-Su sunrise-sunset",
        "Mo-Fr 10:00+",
        'Mo-Fr 09:00-18:00 "by appointment"',
        "Mo-Fr 09:00-18:00, Sa 10:00-12:00",  # a second rule after a comma
        "Mo[1] 10:00-12:00",
        "week 1-10 Mo-Fr 09:00-18:00",
        "Mo-Fr 09:00-25:00",
        "Mo-Fr 09:70-18:00",
        "Mo-Fr 09:00-09:00",
        "Mo-Fr",
        "Mo-PH 09:00-18:00",
        "by appointment",
        "",
        "   ",
        "x" * 400,
    ],
)
def test_what_is_not_understood_is_unknown(text):
    assert state(text, WE, "12:00") == "unknown"


def test_not_text_is_unknown():
    assert run_js("H.evaluate(null, 0, 600).state") == "unknown"
    assert run_js("H.evaluate(42, 0, 600).state") == "unknown"


# ------------------------------------------------------------------------------ time and zone


def test_the_local_weekday_and_time_in_a_zone():
    # 2026-10-21 (Wednesday) 12:30 UTC
    assert run_js("H.localParts(Date.UTC(2026,9,21,12,30), 'Europe/Berlin')") == {
        "day": WE,
        "minute": 14 * 60 + 30,  # CEST, UTC+2
    }
    assert run_js("H.localParts(Date.UTC(2026,9,21,12,30), 'Asia/Tokyo')") == {
        "day": WE,
        "minute": 21 * 60 + 30,
    }
    # close to midnight the weekday follows the zone
    assert run_js("H.localParts(Date.UTC(2026,9,21,23,30), 'Europe/Berlin')") == {
        "day": TH,
        "minute": 90,
    }
    # the winter offset after the clocks go back (2026-10-25)
    assert run_js("H.localParts(Date.UTC(2026,9,26,12,0), 'Europe/Berlin')")["minute"] == 13 * 60


def test_midnight_is_zero_not_twenty_four():
    assert run_js("H.localParts(Date.UTC(2026,9,21,22,0), 'Europe/Berlin')") == {
        "day": TH,
        "minute": 0,
    }


@pytest.mark.parametrize("zone", ["Not/AZone", "", None, 5])
def test_an_unknown_zone_is_null(zone):
    assert run_js(f"H.localParts(Date.UTC(2026,9,21,12,0), {json.dumps(zone)})") is None


RIDE = {
    "departure": "2026-10-21T08:00:00Z",
    "arrival": "2026-10-21T12:00:00Z",
    "distance_m": 40000,
    "timezone": "Europe/Berlin",
}


def passing(poi, ride=RIDE):
    return run_js(f"H.passingMs({json.dumps(poi)}, {json.dumps(ride)})")


def test_the_passing_time_is_the_share_of_the_ride():
    start = run_js("Date.parse('2026-10-21T08:00:00Z')")
    assert passing({"along_route_km": 20}) == start + 2 * 3600 * 1000  # half way: two hours in
    assert passing({"along_route_km": 0}) == start
    assert passing({"along_route_km": 99}) == start + 4 * 3600 * 1000  # past the end: arrival
    assert passing({"along_route_km": -3}) == start


@pytest.mark.parametrize(
    ("poi", "ride"),
    [
        ({}, RIDE),
        ({"along_route_km": None}, RIDE),
        ({"along_route_km": 5}, {**RIDE, "distance_m": 0}),
        ({"along_route_km": 5}, {**RIDE, "departure": "garbage"}),
        ({"along_route_km": 5}, {**RIDE, "arrival": "2026-10-21T07:00:00Z"}),  # before departure
        ({"along_route_km": 5}, None),
    ],
)
def test_without_a_usable_ride_there_is_no_passing_time(poi, ride):
    assert passing(poi, ride) is None


def check(poi, ride=RIDE):
    return run_js(f"H.check({json.dumps(poi)}, {json.dumps(ride)})")


def test_the_popup_line_says_open_with_the_local_time_and_closing():
    # half way = 10:00 UTC = 12:00 in Berlin on a Wednesday
    got = check({"along_route_km": 20, "opening_hours": "Mo-Fr 09:00-18:00"})
    assert got == {"state": "open", "text": "Open when you pass (about 12:00) until 18:00."}


def test_the_popup_line_says_closed_and_when_it_opens():
    got = check({"along_route_km": 0, "opening_hours": "Mo-Fr 10:00-18:00"})  # 10:00 Berlin: open
    assert got["state"] == "open"
    early = {**RIDE, "departure": "2026-10-21T05:00:00Z", "arrival": "2026-10-21T09:00:00Z"}
    got = check({"along_route_km": 0, "opening_hours": "Mo-Fr 10:00-18:00"}, early)  # 07:00 local
    assert got == {"state": "closed", "text": "Closed when you pass (about 07:00), opens 10:00."}


def test_the_zone_decides_not_the_browser():
    tokyo = {**RIDE, "timezone": "Asia/Tokyo"}  # 10:00 UTC is 19:00 there
    got = check({"along_route_km": 20, "opening_hours": "Mo-Fr 09:00-18:00"}, tokyo)
    assert got["state"] == "closed" and "about 19:00" in got["text"]


def test_holidays_and_around_the_clock_are_worded():
    got = check({"along_route_km": 20, "opening_hours": "Mo-Sa 09:00-18:00; PH off"})
    assert got["text"].endswith("Public holidays are not checked.")
    assert "around the clock" in check({"along_route_km": 20, "opening_hours": "24/7"})["text"]
    assert (
        "until midnight"
        in check({"along_route_km": 20, "opening_hours": "Mo-Fr 06:00-24:00"})["text"]
    )


@pytest.mark.parametrize(
    "poi",
    [
        {"along_route_km": 20},  # no hours
        {"along_route_km": 20, "opening_hours": "by appointment"},  # not understood
        {"opening_hours": "Mo-Fr 09:00-18:00"},  # not along a route
    ],
)
def test_nothing_is_claimed_without_hours_or_position(poi):
    assert check(poi) is None


def test_nothing_is_claimed_without_a_zone_or_a_ride():
    poi = {"along_route_km": 20, "opening_hours": "Mo-Fr 09:00-18:00"}
    assert check(poi, {**RIDE, "timezone": None}) is None
    assert check(poi, {**RIDE, "timezone": "Not/AZone"}) is None
    assert run_js(f"H.check({json.dumps(poi)}, null)") is None


def test_the_clock_text():
    assert run_js("[H.clock(0), H.clock(9*60+5), H.clock(1440+120), H.clock(23*60+59)]") == [
        "00:00",
        "09:05",
        "02:00",
        "23:59",
    ]


# ------------------------------------------------------------------------------- page wiring


def test_the_page_loads_hours_before_the_popups_that_use_it_and_the_app_passes_the_ride():
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert page.index('src="hours.js"') < page.index('src="pois.js"') < page.index('src="app.js"')
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "BikeHours.check(poi, rideForHours())" in app
    assert "state.poiTimezone = (entry && data.timezone) || null" in app
    assert (
        "poiTimezone: null" in app and "  state.poiTimezone = null;" in app
    )  # starts empty, reset
