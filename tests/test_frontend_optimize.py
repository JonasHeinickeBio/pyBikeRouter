"""The Optimize tab: the pure helpers in frontend/optimize.js (run under node) and the markup
and wiring the page needs."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "src" / "bike_routing_agent" / "frontend"

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def call(name: str, *args):
    script = (
        f"const O = require({json.dumps(str(FRONTEND / 'optimize.js'))});\n"
        f"const r = typeof O.{name} === 'function' ? O.{name}("
        f"{', '.join(json.dumps(a) for a in args)}) : O.{name};\n"
        "process.stdout.write(JSON.stringify(r));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


REPORT = {
    "summary": "Compared 6 routes (5 distinct) for less traffic and more asphalt. <b>x</b>",
    "goals": [
        {"metric": "traffic", "label": "less traffic"},
        {"metric": "paved", "label": "more asphalt"},
    ],
    "dropped_goals": [{"metric": "pois", "reason": "the POI lookup was not available"}],
    "pois": ["Burg <i>Dankwarderode</i>", "Okerturm"],
    "tradeoffs": ["brouter/commuter has traffic index 0.06 but is 3.0 km shorter"],
    "table": [
        {"selected": True, "feasible": True, "label": "brouter/trekking", "distance_km": 66.04,
         "detour_pct": 13.4, "utility": 0.884,
         "values": {"traffic": 0.12, "paved": 0.97},
         "shown": {"traffic": "traffic index 0.12", "paved": "97 % paved"}},
        {"selected": False, "feasible": False, "label": "brouter/<script>", "distance_km": 95.0,
         "detour_pct": 63.5, "utility": 0.31, "values": {"traffic": 0.01},
         "shown": {"traffic": "traffic index 0.01"}},
    ],
}  # fmt: skip


@needs_node
def test_a_choice_becomes_the_spec_the_api_takes():
    built = call(
        "buildSpec",
        {
            "goals": [{"metric": "traffic", "level": "high"}, {"metric": "paved", "level": "low"}],
            "maxDetourPct": "25",
            "effort": "thorough",
            "poi": {"corridorM": "500", "minFame": "10"},
        },
    )
    assert built == {
        "spec": {
            "goals": [{"metric": "traffic", "weight": 1.0}, {"metric": "paved", "weight": 0.4}],
            "effort": "thorough",
            "max_detour_pct": 25,
        }
    }  # no sights goal: its options are not sent


@needs_node
def test_detours_are_sent_only_when_asked_for_because_off_is_the_default():
    base = {"goals": [{"metric": "traffic", "level": "high"}], "effort": "normal"}
    assert call("buildSpec", {**base, "detours": True})["spec"]["detours"] is True
    assert "detours" not in call("buildSpec", {**base, "detours": False})["spec"]
    assert "detours" not in call("buildSpec", base)["spec"]  # not asked: the server's default


@needs_node
def test_the_sights_options_are_sent_only_with_the_sights_goal():
    built = call(
        "buildSpec",
        {
            "goals": [{"metric": "pois", "level": "medium"}],
            "maxDetourPct": 30,
            "effort": "normal",
            "poi": {"corridorM": "600", "minFame": "20"},
        },
    )
    assert built["spec"]["goals"] == [{"metric": "pois", "weight": 0.7}]
    assert built["spec"]["poi"] == {"corridor_m": 600, "min_fame": 20}


@needs_node
@pytest.mark.parametrize(
    ("choice", "message"),
    [
        ({"goals": []}, "at least one goal"),
        ({"goals": [{"metric": "beauty", "level": "high"}]}, "at least one goal"),
        (
            {
                "goals": [
                    {"metric": m, "level": "high"}
                    for m in ("traffic", "paved", "pois", "ascent", "duration")
                ]
            },
            "at most 4",
        ),
        (
            {
                "goals": [
                    {"metric": "paved", "level": "high"},
                    {"metric": "unpaved", "level": "high"},
                ]
            },
            "contradict",
        ),
        (
            {
                "goals": [
                    {"metric": "traffic", "level": "high"},
                    {"metric": "traffic", "level": "low"},
                ]
            },
            "once",
        ),
    ],
)
def test_a_choice_the_api_would_reject_is_refused_before_sending(choice, message):
    built = call("buildSpec", choice)
    assert message in built["error"] and "spec" not in built


@needs_node
def test_unusable_numbers_fall_back_to_the_servers_defaults():
    spec = call(
        "buildSpec",
        {"goals": [{"metric": "traffic", "level": "high"}], "maxDetourPct": "lots", "effort": "x"},
    )["spec"]
    assert "max_detour_pct" not in spec and spec["effort"] == "normal"


@needs_node
def test_the_report_is_escaped_and_rows_carry_their_map_index():
    html = call("reportHtml", REPORT)
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<i>" not in html and "Burg &lt;i&gt;Dankwarderode&lt;/i&gt;" in html
    assert 'data-index="0"' in html and 'data-index="1"' in html
    assert 'class="cmp-row selected"' in html  # not .opt-row: the weather card owns that name
    assert "best fit" in html and "over limit" in html
    assert ">Traffic</th>" in html and ">Paved</th>" in html  # short headings
    assert ">0.12<" in html and "97&nbsp;%" not in html and "97\u00a0%" in html
    assert 'title="traffic index 0.12"' in html  # the long form stays as the hover text
    assert "&mdash;" in html  # a goal a route has no value for
    assert "66.0 km" in html and "+13&nbsp;%" in html
    assert "Not applied: pois (the POI lookup was not available)" in html
    assert "not a count of cars" in html


@needs_node
def test_nothing_is_shown_without_a_report():
    assert call("reportHtml", None) == "" and call("reportHtml", {"summary": ""}) == ""
    assert call("tableHtml", {"table": [], "goals": []}) == ""


@needs_node
def test_a_failed_sight_lookup_is_said_and_the_wait_is_described():
    html = call(
        "reportHtml", {"summary": "s", "poi_status": "unavailable", "table": [], "goals": []}
    )
    assert "sight lookup was not available" in html
    assert call("waitText", "quick") == "about half a minute"
    assert "minute" in call("waitText", "normal") and "several" in call("waitText", "thorough")


@needs_node
def test_the_presets_only_use_goals_the_tab_offers():
    metrics = {g["metric"] for g in call("GOALS")}
    for preset in call("PRESETS"):
        assert {g["metric"] for g in preset["goals"]} <= metrics
        assert call("buildSpec", {"goals": preset["goals"]}).get("spec")


def test_the_page_ships_the_tabs_hidden_until_the_server_can_optimise():
    html = (FRONTEND / "index.html").read_text()
    assert '<body data-tab="plan">' in html  # the optimiser's panels are hidden by default
    assert re.search(r'<nav class="tabs" id="mode-tabs"[^>]*\bhidden\b', html)
    assert 'id="optimize-panel" data-tab="optimize"' in html
    assert 'id="optimization-panel" data-tab="optimize"' in html
    assert html.index('src="optimize.js"') < html.index('src="app.js"')
    css = (FRONTEND / "styles.css").read_text()
    assert 'body[data-tab="optimize"] [data-tab="plan"]' in css
    assert 'body[data-tab="plan"] [data-tab="optimize"]' in css


def test_the_tab_asks_the_same_endpoint_with_an_optimize_spec():
    app = (FRONTEND / "app.js").read_text()
    assert "caps.optimizer" in app and "payload.optimize = built.spec" in app
    assert "delete payload.poi_stops" in app  # sights are a goal there, not stops
    assert "renderOptimization(data.optimization)" in app
    # a plan from the other tab takes over, and a slow answer cannot redraw
    assert "generation !== state.planGeneration" in app


@needs_node
def test_values_are_shown_compactly_and_the_engine_prefix_is_dropped_when_it_is_the_same():
    cases = {
        ("traffic", 0.1234): "0.12",
        ("paved", 0.974): "97\u00a0%",
        ("busy_roads", 0.05): "5\u00a0%",
        ("pois", 8.0): "8",
        ("distance", 66040): "66.0\u00a0km",
        ("duration", 9646): "2.7\u00a0h",
        ("ascent", 301.2): "301\u00a0m",
    }
    for (metric, value), shown in cases.items():
        assert call("compact", metric, value) == shown
    assert call("compact", "traffic", None) is None
    html = call("tableHtml", REPORT)
    assert ">trekking" in html and "brouter/trekking" not in html  # one engine: no prefix


def test_the_reset_button_also_unticks_the_detours_box():
    source = (FRONTEND / "app.js").read_text()
    body = source[source.index("function resetAll") :]
    body = body[: body.index("\n}\n")]
    assert "els.optimizeDetours.checked = false" in body
