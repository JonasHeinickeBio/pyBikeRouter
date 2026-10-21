"""frontend/cues.js: wording the turn-by-turn steps and the cue sheet, under node."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "src" / "bike_routing_agent" / "frontend"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_js(expression: str, tz: str = "Europe/Berlin"):
    script = (
        f"const C = require({json.dumps(str(FRONTEND / 'cues.js'))});\n"
        f"process.stdout.write(JSON.stringify({expression}));"
    )
    env = {**os.environ, "TZ": tz}  # the clock is the viewer's local time: pin it
    result = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, timeout=30, env=env
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def cue(kind, distance_m, **kw):
    return {"kind": kind, "distance_m": distance_m, "lon": 10.0, "lat": 52.0, **kw}


def steps(cues, total):
    return run_js(f"C.steps({json.dumps(cues)}, {json.dumps(total)})")


@pytest.mark.parametrize(
    ("c", "expected"),
    [
        (cue("left", 0), "Turn left"),
        (cue("right", 0), "Turn right"),
        (cue("slight_left", 0), "Turn slightly left"),
        (cue("slight_right", 0), "Turn slightly right"),
        (cue("sharp_left", 0), "Turn sharp left"),
        (cue("sharp_right", 0), "Turn sharp right"),
        (cue("keep_left", 0), "Keep left"),
        (cue("keep_right", 0), "Keep right"),
        (cue("uturn", 0), "Make a U-turn"),
        (cue("roundabout", 0, exit=1), "At the roundabout take the 1st exit"),
        (cue("roundabout", 0, exit=2), "At the roundabout take the 2nd exit"),
        (cue("roundabout", 0, exit=3), "At the roundabout take the 3rd exit"),
        (cue("roundabout", 0, exit=4), "At the roundabout take the 4th exit"),
        (cue("roundabout", 0), "Go through the roundabout"),
        (cue("teleport", 0), ""),
        (None, ""),
    ],
)
def test_each_kind_is_worded(c, expected):
    assert run_js(f"C.text({json.dumps(c)})") == expected


def test_every_kind_has_a_glyph_and_unknown_has_none():
    kinds = ["left", "right", "slight_left", "slight_right", "sharp_left", "sharp_right",
             "keep_left", "keep_right", "uturn", "roundabout"]  # fmt: skip
    glyphs = [run_js(f"C.glyph({{kind: {json.dumps(k)}}})") for k in kinds]
    assert all(glyphs)
    assert run_js("C.glyph({kind: 'x'})") == "" and run_js("C.glyph(null)") == ""


@pytest.mark.parametrize(
    ("meters", "expected"),
    [(0, "0 m"), (7, "7 m"), (99.4, "99 m"), (123, "120 m"), (995, "1000 m"), (1000, "1.0 km"),
     (1234, "1.2 km"), (31623, "31.6 km"), (-5, ""), (None, "")],
)  # fmt: skip
def test_distances_read_naturally(meters, expected):
    assert run_js(f"C.distanceText({json.dumps(meters)})") == expected


def test_the_steps_run_from_start_to_arrival_with_the_distance_to_the_next():
    rows = steps([cue("left", 400), cue("right", 1500, angle_deg=80)], 5000)
    assert [r["text"] for r in rows] == ["Start", "Turn left", "Turn right", "Arrive"]
    assert [r["at"] for r in rows] == ["0 m", "400 m", "1.5 km", "5.0 km"]
    assert [r["leg"] for r in rows] == ["400 m", "1.1 km", "3.5 km", ""]
    assert rows[1]["cue"]["kind"] == "left" and rows[0]["cue"] is None and rows[3]["legM"] is None


def test_no_cues_no_steps():
    assert steps([], 5000) == [] and steps(None, 5000) == []
    assert steps([cue("teleport", 10)], 5000) == []  # nothing usable: no list at all


def test_cues_out_of_order_or_beyond_the_route_are_dropped_not_trusted():
    rows = steps(
        [cue("left", 500), cue("right", 300), cue("keep_left", 900), cue("uturn", 9000)], 1000
    )
    assert [r["text"] for r in rows] == ["Start", "Turn left", "Keep left", "Arrive"]


def test_a_cue_a_few_metres_past_the_engine_length_stays_and_the_end_follows_it():
    # the cue distances are measured on the drawn shape, the total is the engine's
    rows = steps([cue("left", 1030)], 1000)
    assert [r["text"] for r in rows] == ["Start", "Turn left", "Arrive"]
    assert rows[2]["at"] == "1.0 km" and rows[1]["legM"] == 0


def test_without_a_total_the_last_cue_ends_the_route():
    rows = steps([cue("left", 400)], None)
    assert rows[-1]["text"] == "Arrive" and rows[-1]["at"] == "400 m"


def test_the_summary_counts_turns():
    one = steps([cue("left", 400)], 1000)
    many = steps([cue("left", 400), cue("right", 500)], 1000)
    assert run_js(f"C.summary({json.dumps(one)})") == "1 turn"
    assert run_js(f"C.summary({json.dumps(many)})") == "2 turns"
    assert run_js("C.summary([])") == "" and run_js("C.summary(null)") == ""


def test_the_list_is_buttons_and_everything_is_escaped():
    rows = steps([cue("left", 400)], 1000)
    rows[1]["text"] = "<img src=x onerror=alert(1)>"
    html = run_js(f"C.listHtml({json.dumps(rows)})")
    assert html.count('<button type="button" class="cue-row"') == 3
    assert 'data-cue="1"' in html and "then 400 m" in html
    assert "<img" not in html and "&lt;img" in html


def test_the_cue_sheet_is_plain_text_with_a_title_and_a_note():
    rows = steps([cue("left", 400), cue("roundabout", 1500, exit=2)], 5000)
    sheet = run_js(f"C.sheetText({json.dumps(rows)}, 'My ride')")
    lines = sheet.splitlines()
    assert lines[0] == "My ride" and lines[1] == ""
    assert lines[2].strip() == "0 m  Start  (then 400 m)"
    assert "Turn left  (then 1.1 km)" in lines[3]
    assert "At the roundabout take the 2nd exit  (then 3.5 km)" in lines[4]
    assert lines[5].strip().endswith("Arrive")
    assert "no street names" in sheet and sheet.endswith("\n")
    assert run_js("C.sheetText([], null)").startswith("Cue sheet\n")


RIDE = {"departure": "2026-10-21T08:00:00Z", "arrival": "2026-10-21T12:00:00Z"}


def timed(cues, total, ride=RIDE, tz="Europe/Berlin"):
    return run_js(f"C.steps({json.dumps(cues)}, {json.dumps(total)}, {json.dumps(ride)})", tz)


def test_with_a_ride_each_step_has_the_time_you_are_expected_there():
    rows = timed([cue("left", 1000), cue("right", 3000)], 4000)
    # 08:00 UTC is 10:00 in Berlin (CEST); the ride is four hours at an even pace
    assert [r["clock"] for r in rows] == ["10:00", "11:00", "13:00", "14:00"]


def test_the_clock_is_the_viewers_local_time():
    rows = timed([cue("left", 1000)], 4000, tz="America/New_York")  # EDT, UTC-4
    assert [r["clock"] for r in rows] == ["04:00", "05:00", "08:00"]


def test_without_a_usable_ride_the_rows_carry_no_time():
    for ride in (None, {}, {"departure": "x", "arrival": "y"},
                 {"departure": RIDE["arrival"], "arrival": RIDE["departure"]}):  # fmt: skip
        assert {r["clock"] for r in timed([cue("left", 1000)], 4000, ride)} == {""}
    assert {r["clock"] for r in steps([cue("left", 1000)], 4000)} == {""}  # the two-argument form


def test_without_a_total_the_last_cue_ends_the_ride():
    rows = timed([cue("left", 1000)], None)  # 1000 m is then the whole route: the cue is the end
    assert [r["clock"] for r in rows] == ["10:00", "14:00", "14:00"]


def test_the_list_and_the_sheet_show_the_times_only_when_there_are_some():
    rows = timed([cue("left", 1000)], 4000)
    html = run_js(f"C.listHtml({json.dumps(rows)})")
    assert html.count('class="cue-time"') == 3 and "~11:00" in html
    sheet = run_js(f"C.sheetText({json.dumps(rows)}, 'Ride')")
    assert "~10:00" in sheet and "~11:00  Turn left" in sheet
    assert "Times (~) are estimates at an even pace" in sheet
    plain = steps([cue("left", 1000)], 4000)
    assert "cue-time" not in run_js(f"C.listHtml({json.dumps(plain)})")
    plain_sheet = run_js(f"C.sheetText({json.dumps(plain)}, 'Ride')")
    assert "~" not in plain_sheet and "Times (~)" not in plain_sheet


def test_the_clock_text_pads_to_two_digits():
    assert run_js("C.clockText(Date.UTC(2026, 9, 21, 7, 5))", "UTC") == "07:05"


# ------------------------------------------------------------------------------ page wiring


def test_the_page_has_the_card_and_the_app_fills_and_clears_it():
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert 'id="cues-card"' in page and 'id="cues-download"' in page
    assert page.index('src="cues.js"') < page.index('src="app.js"')
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "renderCues(cand);" in app
    assert "els.cuesCard.hidden = true;" in app and "state.cueRows = [];" in app
    assert "BikeCues.steps(cand.cues, cand.metrics && cand.metrics.distance_m, ride)" in app
