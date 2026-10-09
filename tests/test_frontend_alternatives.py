"""The pure helpers in frontend/alternatives.js, run under node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "src" / "bike_routing_agent" / "frontend"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_js(expression: str):
    script = (
        f"const A = require({json.dumps(str(FRONTEND / 'alternatives.js'))});\n"
        f"process.stdout.write(JSON.stringify({expression}));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def cand(profile="custom_touring-v1", provider="brouter", **kw):
    return {
        "provider": provider,
        "provider_profile": profile,
        "metrics": {"distance_m": 14180, "duration_s": 1980, "ascent_m": 28.6},
        "pros": [],
        "cons": [],
        **kw,
    }


def test_labels_are_human_and_unknown_profiles_stay_visible():
    assert run_js(f"A.styleLabel({json.dumps(cand())})") == "Touring · brouter"
    assert run_js(f"A.styleLabel({json.dumps(cand('mtb'))})") == "Mountain bike · brouter"
    assert run_js(f"A.styleLabel({json.dumps(cand('weird', 'x'))})") == "weird · x"


def test_the_summary_line_rounds_and_skips_missing_values():
    assert run_js(f"A.summaryLine({json.dumps(cand())})") == "14.2 km · 33 min · ↑ 29 m"
    assert run_js("A.summaryLine({metrics: {distance_m: 500}})") == "0.5 km"


def test_near_copies_are_not_shown_as_alternatives():
    candidates = [cand(), cand(duplicate_of="brouter/x"), cand("mtb")]
    assert run_js(f"A.distinctIndices({json.dumps(candidates)})") == [0, 2]


def test_nothing_is_rendered_for_a_single_distinct_route():
    assert run_js(f"A.alternativesHtml({json.dumps([cand()])}, ['#000'], 0, 0)") == ""
    pair = [cand(), cand(duplicate_of="brouter/x")]
    assert run_js(f"A.alternativesHtml({json.dumps(pair)}, ['#000', '#111'], 0, 0)") == ""


def test_cards_show_pros_and_cons_mark_the_selection_and_escape_text():
    candidates = [
        cand(pros=["Shortest (14.2 km)"], duplicates=["brouter/y"]),
        cand("mtb", cons=["<b>12 min slower than the fastest</b>"], pros=[]),
    ]
    html = run_js(f"A.alternativesHtml({json.dumps(candidates)}, ['#0e7a4a', '#2563eb'], 0, 1)")
    assert html.count("<button") == 2
    assert 'data-candidate="0"' in html and 'data-candidate="1"' in html
    assert html.count("Top ranked") == 1
    assert 'class="alt-card selected"' in html and 'class="alt-card active"' in html
    assert "Shortest (14.2 km)" in html and "alt-pros" in html and "alt-cons" in html
    assert "<b>12 min" not in html and "&lt;b&gt;12 min" in html  # server text is escaped
    assert "1 near-identical route merged into this one" in html


def test_a_route_without_differences_says_so():
    html = run_js(f"A.cardHtml({json.dumps(cand())}, 0, '#000', false, false)")
    assert "No notable difference from the others." in html
