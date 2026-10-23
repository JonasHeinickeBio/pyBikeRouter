"""frontend/stream.js: progress while a plan is made, and where its time went, under node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bike_routing_agent.tracing import STEP_LABELS

NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "src" / "bike_routing_agent" / "frontend"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

J = json.dumps


def run_js(body: str):
    script = (
        f"const S = require({json.dumps(str(FRONTEND / 'stream.js'))});\n"
        f"process.stdout.write(JSON.stringify((() => {{ {body} }})()));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ------------------------------------------------------------------------------ parser


def test_the_labels_are_the_servers():
    labels = run_js("return S.LABELS;")
    assert labels == STEP_LABELS
    assert [node for node, _ in run_js("return S.STEPS;")] == list(STEP_LABELS)  # same order


def test_whole_events_are_read_in_order():
    text = (
        sse("step", {"node": "parse_request", "label": "Reading the request", "ms": 1.0})
        + sse("route", {"geometry_geojson": None})
        + sse("result", {"status": "ready"})
    )
    got = run_js(f"return S.createParser().push({J(text)});")
    assert [e["event"] for e in got] == ["step", "route", "result"]
    assert got[2]["data"] == {"status": "ready"}


def test_an_event_cut_into_pieces_waits_for_its_rest():
    text = sse("step", {"node": "route_with_provider", "ms": 12.5}) + sse("result", {"a": 1})
    for size in (1, 3, 7, 50):
        pieces = [text[i : i + size] for i in range(0, len(text), size)]
        got = run_js(
            f"const p = S.createParser(); const out = []; for (const c of {J(pieces)}) "
            "out.push(...p.push(c)); return out.map(e => e.event);"
        )
        assert got == ["step", "result"], size


def test_crlf_comments_unknown_events_and_bad_json_are_skipped_not_thrown():
    text = (
        ": keep-alive\n\n"
        'event: step\r\ndata: {"node": "parse_request"}\r\n\r\n'
        'event: surprise\ndata: {"x": 1}\n\n'
        "event: step\ndata: {not json\n\n"
        "event: step\n\n"
        'data: {"no": "event name"}\n\n'
        'event: result\ndata: {"ok":\ndata: true}\n\n'  # multi-line data joins with a newline
    )
    got = run_js(f"return S.createParser().push({J(text)});")
    assert [e["event"] for e in got] == ["step", "result"]
    assert got[0]["data"] == {"node": "parse_request"} and got[1]["data"] == {"ok": True}


def test_something_that_is_not_an_event_stream_does_not_grow_forever():
    got = run_js(
        """const p = S.createParser();
        const big = 'x'.repeat(1024 * 1024);
        for (let i = 0; i < 6; i++) p.push(big);
        p.push('\\n\\n'); // the junk ends somewhere, and the next real event is read again
        return p.push('event: result\\ndata: {"a":1}\\n\\n').map(e => e.event);"""
    )
    assert got == ["result"]


# ------------------------------------------------------------------------------ words and lines


def test_progress_says_which_step_of_how_many():
    assert (
        run_js('return S.progressText({node: "route_with_provider", label: "Routing"});')
        == "Routing… (step 5 of 11)"
    )
    # a label the server did not send comes from the table; an unknown step is just its label
    assert run_js('return S.progressText({node: "enrich_candidates"});').startswith(
        "Reading the road data…"
    )
    assert run_js('return S.progressText({node: "x", label: "Something new"});') == (
        "Something new…"
    )
    for odd in ("null", '"text"', "{}", '{node: "nope"}'):
        assert run_js(f"return S.progressText({odd});") == "Planning route…"


def test_the_early_route_becomes_latlng_pairs_and_junk_is_dropped():
    line = {
        "geometry_geojson": {
            "type": "LineString",
            "coordinates": [[10.5, 52.2, 80], [10.6, 52.3], [1000, 5], ["a", 1], [10.7, 52.4]],
        }
    }
    assert run_js(f"return S.provisionalLine({J(line)});") == [
        [52.2, 10.5],
        [52.3, 10.6],
        [52.4, 10.7],
    ]
    for bad in (
        "null",
        "{}",
        '{geometry_geojson: {type: "Point", coordinates: [1, 2]}}',
        '{geometry_geojson: {type: "LineString", coordinates: [[1, 2]]}}',
        '{geometry_geojson: {type: "LineString", coordinates: "x"}}',
    ):
        assert run_js(f"return S.provisionalLine({bad});") is None


# ------------------------------------------------------------------------------ where time went

TRACE = [
    {"node": "parse_request", "ms": 1.0, "status": "in_progress", "ok": True},
    {"node": "route_with_provider", "ms": 3000.0, "status": "in_progress", "ok": True},
    {"node": "explain_and_export", "ms": 1000.0, "status": "ready", "ok": True},
]


def test_the_breakdown_lists_steps_in_order_with_shares_and_marks_the_slowest():
    view = run_js(f"return S.timeBreakdown({J(TRACE)});")
    assert view["total_ms"] == 4001.0 and view["slowest"] == "route_with_provider"
    assert [r["node"] for r in view["rows"]] == [t["node"] for t in TRACE]
    routing = view["rows"][1]
    assert routing["slowest"] is True and routing["width"] == 1.0
    assert abs(routing["share"] - 3000 / 4001) < 1e-9
    assert [r["slowest"] for r in view["rows"]] == [False, True, False]
    assert routing["label"] == "Routing"


def test_a_trace_from_outside_is_checked():
    dirty = [
        TRACE[0],
        None,
        "x",
        {"node": 5, "ms": 1},
        {"node": "a", "ms": -4},
        {"node": "b", "ms": "slow"},
        {"node": "<img src=x>", "ms": 7.0, "ok": False},
    ]
    view = run_js(f"return S.timeBreakdown({J(dirty)});")
    assert [r["node"] for r in view["rows"]] == ["parse_request", "<img src=x>"]
    assert view["rows"][1]["ok"] is False
    for nothing in ("null", "[]", '"x"', "{}", "[null]"):
        assert run_js(f"return S.timeBreakdown({nothing});") is None


def test_an_all_zero_trace_does_not_divide_by_zero():
    view = run_js('return S.timeBreakdown([{node: "parse_request", ms: 0}]);')
    assert view["rows"][0]["share"] == 0 and view["rows"][0]["width"] == 0


def test_times_read_like_a_person_would_say_them():
    values = "[0, 820.4, 999.6, 4200, 59999, 60000, 125000, -1, NaN]"
    assert run_js(f"return {values}.map(S.formatMs);") == [
        "0 ms",
        "820 ms",
        "1000 ms",
        "4.2 s",
        "60.0 s",
        "1 min 0 s",
        "2 min 5 s",
        "",
        "",
    ]


def test_the_html_is_escaped_and_marks_the_slowest_and_the_failed():
    trace = [*TRACE, {"node": "<script>alert(1)</script>", "ms": 5, "ok": False}]
    html = run_js(f"return S.breakdownHtml({J(trace)}, false);")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert 'class="slowest"' in html and 'class=" failed"' in html
    assert "4.0 s in total" in html and "Routing" in html


def test_a_cached_plan_says_nothing_was_computed_and_no_trace_shows_nothing():
    assert "nothing was computed" in run_js("return S.breakdownHtml(null, true);")
    assert run_js("return S.breakdownHtml(null, false);") == ""
    assert run_js("return S.breakdownHtml([], false);") == ""


def test_the_summary_names_the_slowest_step():
    assert run_js(f"return S.summaryText({J(TRACE)});") == "4.0 s, mostly routing"
    assert run_js("return S.summaryText(null);") == ""


# ------------------------------------------------------------------------------ page wiring


def test_the_page_loads_the_script_before_app_js_and_has_the_card():
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert 'id="trace-card"' in page and "Where the time went" in page
    assert page.index('src="stream.js"') < page.index('src="app.js"')
    css = (FRONTEND / "styles.css").read_text(encoding="utf-8")
    assert ".trace-list" in css and ".trace-card" in css


def test_the_form_asks_the_stream_and_falls_back_to_the_plain_endpoint():
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "/v1/route/plan/stream" in app
    # an older server (no stream endpoint) is asked the plain way, with the trace
    assert "`${API_BASE}/v1/route/plan?trace=true`" in app
    assert "resp.status === 404 || resp.status === 405" in app
    # the early route is its own faint layer, cleared when the result replaces it or the plan fails
    assert "state.provisionalLayer.clearLayers();" in app
    assert "if (!drawn) state.provisionalLayer.clearLayers();" in app
    # a stale plan (a newer one took over) never touches the status line or the map
    assert "else if (!isCurrent()) continue;" in app
    # the result renders the card, and a chat plan (no trace) hides it
    assert "renderTrace(data);" in app and "els.traceCard.hidden = true;" in app
