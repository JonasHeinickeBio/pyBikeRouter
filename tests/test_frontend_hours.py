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
    assert at("24/7", WE, "03:00") == {
        "state": "open",
        "until": None,
        "openEnd": False,
        "holidays": False,
    }
    assert at("Mo-Su 00:00-24:00", SU, "23:59")["until"] is None  # goes on into the next day
    assert at("Mo-Fr 00:00-24:00", FR, "23:00")["until"] == 1440  # but not into the weekend
    assert state("Mo-Fr 09:00-24:00", MO, "23:30") == "open"


def test_hours_that_run_past_midnight_open_the_next_morning_too():
    text = "Fr,Sa 20:00-02:00"
    assert state(text, FR, "23:00") == "open"
    assert at(text, SA, "01:00") == {
        "state": "open",
        "until": 120,
        "openEnd": False,
        "holidays": False,
    }
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
        "Mo-Su sunrise-sunset",
        'Mo-Fr 09:00-18:00 "by appointment"',
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


def day_minute_month(expression: str) -> dict:
    """localParts without the calendar date (tested on its own below)."""
    got = run_js(expression)
    return {key: got[key] for key in ("day", "minute", "month")}


def test_the_local_weekday_and_time_in_a_zone():
    # 2026-10-21 (Wednesday) 12:30 UTC
    assert day_minute_month("H.localParts(Date.UTC(2026,9,21,12,30), 'Europe/Berlin')") == {
        "day": WE,
        "minute": 14 * 60 + 30,  # CEST, UTC+2
        "month": 10,
    }
    assert day_minute_month("H.localParts(Date.UTC(2026,9,21,12,30), 'Asia/Tokyo')") == {
        "day": WE,
        "minute": 21 * 60 + 30,
        "month": 10,
    }
    # close to midnight the weekday follows the zone
    assert day_minute_month("H.localParts(Date.UTC(2026,9,21,23,30), 'Europe/Berlin')") == {
        "day": TH,
        "minute": 90,
        "month": 10,
    }
    # the winter offset after the clocks go back (2026-10-25)
    assert run_js("H.localParts(Date.UTC(2026,9,26,12,0), 'Europe/Berlin')")["minute"] == 13 * 60


def test_midnight_is_zero_not_twenty_four():
    assert day_minute_month("H.localParts(Date.UTC(2026,9,21,22,0), 'Europe/Berlin')") == {
        "day": TH,
        "minute": 0,
        "month": 10,
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


# ------------------------------------------------ the syntax seen in real data (Hannover sample)


def test_spaces_anywhere_do_not_matter():
    assert state("Mo, Th 11:00 - 14:15, 16:50 - 22:20", MO, "12:00") == "open"
    assert state("Mo-Th, Su 09:00-00:00; Fr, Sa 09:00-01:00", FR, "23:00") == "open"
    assert state("Mo - Fr 09:00 - 18:00", WE, "10:00") == "open"
    assert state("Mo-Fr  09:00-18:00 ;  Sa 10:00-14:00", SA, "11:00") == "open"


def test_a_rule_added_after_a_comma_adds_to_the_earlier_one():
    text = "Mo-Fr 10:00-12:00, Sa 10:00-14:00"
    assert [state(text, d, "11:00") for d in (MO, FR, SA, SU)] == ["open", "open", "open", "closed"]
    assert state(text, SA, "13:00") == "open" and state(text, MO, "13:00") == "closed"
    # it adds, it does not replace: the weekday stretch and the extra one both count
    both = "Mo-Su 11:30-14:30, Su,Mo 17:30-22:00, Tu-Sa 17:30-23:00"
    assert [state(both, SU, t) for t in ("12:00", "16:00", "18:00")] == ["open", "closed", "open"]
    assert [state(both, MO, t) for t in ("12:00", "18:00", "22:30")] == ["open", "open", "closed"]
    assert state(both, TU, "22:30") == "open"


def test_a_comma_inside_a_day_list_is_not_a_new_rule():
    assert state("Mo,We 10:00-12:00", WE, "11:00") == "open"
    assert state("Mo,We 10:00-12:00", TU, "11:00") == "closed"
    assert state("Mo, We 10:00-12:00; Fr 09:00-10:00", FR, "09:30") == "open"


def test_an_open_end_is_open_from_then_with_no_closing_time():
    assert at("Tu-Su 17:00+", WE, "19:00") == {
        "state": "open", "until": None, "openEnd": True, "holidays": False,
    }  # fmt: skip
    early = at("Tu-Su 17:00+", WE, "16:00")
    assert early["state"] == "closed" and early["opensAt"] == 17 * 60
    assert state("Mo off; Tu-Su 17:00+", MO, "19:00") == "closed"


def test_a_plus_after_a_closing_time_means_at_least_until_then_and_unknown_after():
    text = "Mo-Sa 12:00-23:00+"
    inside = at(text, WE, "20:00")
    assert inside["state"] == "open" and inside["until"] == 23 * 60 and inside["atLeast"] is True
    assert state(text, WE, "23:30") == "unknown"  # the text does not say: no verdict
    assert state(text, WE, "11:00") == "closed"
    assert state(text, SU, "13:00") == "closed"
    assert state("Mo-Sa 12:00-15:00,17:00+; Su 12:00+", WE, "16:00") == "closed"
    assert state("Mo-Sa 12:00-15:00,17:00+; Su 12:00+", WE, "18:00") == "open"


def at_month(text, day, hhmm, month):
    h, m = hhmm.split(":")
    return run_js(f"H.evaluate({json.dumps(text)}, {day}, {int(h) * 60 + int(m)}, {month})")


def test_month_ranges_pick_the_rule_for_the_month():
    text = "Apr-Oct Mo-Su 10:00-18:00; Nov-Mar Mo-Fr 10:00-16:00"
    assert at_month(text, SA, "17:00", 7)["state"] == "open"  # July
    assert at_month(text, SA, "12:00", 1)["state"] == "closed"  # January: weekdays only
    assert at_month(text, WE, "12:00", 1)["state"] == "open"
    assert at_month(text, WE, "17:00", 1)["state"] == "closed"


def test_a_month_range_can_wrap_the_year_and_a_colon_after_it_is_fine():
    assert at_month("Nov-Feb: 10:00-16:00", MO, "12:00", 12)["state"] == "open"
    assert (
        at_month("Nov-Feb: 10:00-16:00", MO, "12:00", 6)["state"] == "closed"
    )  # not listed: closed
    assert at_month("Jan,Dec Mo-Su 10:00-16:00", MO, "12:00", 12)["state"] == "open"
    assert at_month("Feb,Mar,Aug,Sep: Mo-Fr 08:00-16:30; Oct-Jan,Apr-Jul: Mo-Fr 08:00-18:00",
                    WE, "17:00", 5)["state"] == "open"  # fmt: skip


def test_months_without_a_month_are_unknown_not_guessed():
    assert state("Apr-Oct Mo-Su 10:00-18:00", WE, "12:00") == "unknown"


def test_the_popup_line_reads_the_month_and_the_open_end(tmp_path=None):
    ride = {
        "departure": "2026-10-21T08:00:00Z",
        "arrival": "2026-10-21T12:00:00Z",
        "distance_m": 40000,
        "timezone": "Europe/Berlin",
    }  # half way: 10:00 UTC = 12:00 on Wednesday 21 October (month 10)
    winter = {"along_route_km": 20, "opening_hours": "Nov-Mar Mo-Su 10:00-18:00"}
    got = run_js(f"H.check({json.dumps(winter)}, {json.dumps(ride)})")
    assert got["state"] == "closed"  # October is not in Nov-Mar
    autumn = {"along_route_km": 20, "opening_hours": "Apr-Oct Mo-Su 10:00-18:00"}
    assert run_js(f"H.check({json.dumps(autumn)}, {json.dumps(ride)})")["text"].endswith(
        "until 18:00."
    )
    evening = {"along_route_km": 20, "opening_hours": "Mo-Su 11:00+"}
    assert (
        "no closing time given"
        in run_js(f"H.check({json.dumps(evening)}, {json.dumps(ride)})")["text"]
    )
    plus = {"along_route_km": 20, "opening_hours": "Mo-Su 11:00-23:00+"}
    assert (
        "until at least 23:00" in run_js(f"H.check({json.dumps(plus)}, {json.dumps(ride)})")["text"]
    )


SAMPLE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "osm_opening_hours_sample.json").read_text(
        encoding="utf-8"
    )
)


def test_most_real_opening_hours_are_understood():
    """A sample of real opening_hours tags around Hannover (OSM, ODbL): the share read, and nothing
    read wrongly enough to crash or to claim a state it does not have."""
    texts = SAMPLE["texts"]
    result = run_js(
        f"""(() => {{
        const texts = {json.dumps(texts)};
        let understood = 0;
        const states = new Set();
        for (const t of texts) {{
          if (!H.parse(t)) continue;
          understood++;
          for (let day = 0; day < 7; day++)
            for (const minute of [0, 90, 600, 780, 1020, 1380, 1439])
              for (const month of [1, 4, 7, 12])
                states.add(H.evaluate(t, day, minute, month).state);
        }}
        return {{understood, total: texts.length, states: [...states].sort()}};
      }})()"""
    )
    assert result["total"] >= 150
    assert result["understood"] / result["total"] >= 0.92
    assert set(result["states"]) <= {"open", "closed", "unknown"}
    assert {"open", "closed"} <= set(result["states"])


def test_the_pieces_of_real_text_that_stay_unknown_stay_unknown():
    for text in (
        '"zur CeBIT"',
        "Mo-Fr 10:00-18:00; Dec 24 off",  # a day of the month
        "Mar-Nov: Fr[1] 15:00-19:00",  # the first Friday
        "easter-Oct Su 10:00-12:00",
        "PH unknown; Mo-Fr 09:00-18:00",
        "Tu-Th 11:30-22:00; Fr,Sa 23:00-23:00",  # start equals end
        "Fr 20:00-02:00+",  # an open end that crosses midnight
    ):
        assert run_js(f"H.parse({json.dumps(text)}) === null") is True, text


def at_date(text, day, hhmm, month, year, date):
    h, m = hhmm.split(":")
    return run_js(
        f"H.evaluate({json.dumps(text)}, {day}, {int(h) * 60 + int(m)}, {month}, {year}, {date})"
    )


def test_the_month_of_the_day_before_and_after_follows_the_calendar_not_the_arrival_month():
    # Friday 31 January 2025 at 23:00: the Friday rule runs to midnight and Saturday 1 February
    # starts at 00:00 (the February rule), so it stays open until 02:00, not "until midnight".
    text = "Jan Fr 20:00-24:00; Feb Sa 00:00-02:00"
    got = at_date(text, FR, "23:00", 1, 2025, 31)
    assert got["state"] == "open" and got["until"] == 120
    # the same Friday inside a month whose next day is the same month: until midnight
    assert at_date("Jan Fr 20:00-24:00", FR, "23:00", 1, 2025, 17)["until"] == 1440


def test_hours_that_run_past_midnight_carry_over_a_new_year():
    # Wednesday 31 December 2025 20:00-02:00, asked at 01:00 on Thursday 1 January 2026
    text = "Dec We 20:00-02:00"
    assert at_date(text, TH, "01:00", 1, 2026, 1)["state"] == "open"
    # without the year and date (a caller that has only the month) the lookup stays as it was
    assert at_month(text, TH, "01:00", 1)["state"] == "closed"


def test_month_arithmetic_for_neighbouring_days():
    assert run_js("H.monthAtOffset(1, 2025, 31, 1)") == 2
    assert run_js("H.monthAtOffset(3, 2025, 1, -1)") == 2
    assert run_js("H.monthAtOffset(12, 2025, 31, 1)") == 1
    assert run_js("H.monthAtOffset(2, 2024, 29, 1)") == 3  # a leap year
    assert run_js("H.monthAtOffset(2, 2025, 28, 1)") == 3
    assert run_js("H.monthAtOffset(6, undefined, undefined, 1)") == 6  # no date: the month stays


def test_the_local_date_comes_with_the_clock():
    got = run_js("H.localParts(Date.UTC(2026, 0, 31, 23, 30), 'Europe/Berlin')")
    assert (got["year"], got["month"], got["date"]) == (2026, 2, 1)  # already the next day there
