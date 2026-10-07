"""The pure helpers in frontend/text-planning.js, run under node."""

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
        f"const T = require({json.dumps(str(FRONTEND / 'text-planning.js'))});\n"
        f"process.stdout.write(JSON.stringify({expression}));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


INTERPRETATION = {
    "request": {
        "origin": "Braunschweig",
        "destination": None,
        "via": ["Wolfenbüttel"],
        "constraints": {
            "bike_type": "gravel",
            "target_distance_km": 50,
            "return_to_origin": True,
            "avoid_ferries": False,
            "prefer_surfaces": ["gravel", "dirt"],
        },
    },
    "departure_time": "2026-10-08T08:00:00+02:00",
    "notes": ["Assumed tomorrow means 2026-10-08.", "<b>x</b>"],
    "parser": {"model": "m"},
}


def test_form_values_keep_unstated_constraints_null():
    values = run_js(f"T.formValues({json.dumps(INTERPRETATION)})")
    assert values["origin"] == "Braunschweig"
    assert values["destination"] == ""
    assert values["via"] == ["Wolfenbüttel"]
    assert values["bikeType"] == "gravel"
    assert values["targetDistance"] == 50
    assert values["maxDistance"] is None
    assert values["avoidTraffic"] is None
    assert values["avoidFerries"] is False
    assert values["loop"] is True
    assert values["prefer"] == "gravel, dirt"
    assert values["avoid"] == ""
    assert values["departureLocal"].startswith("2026-10-0")


def test_form_values_without_interpretation_is_null():
    assert run_js("T.formValues(null)") is None
    assert run_js("T.formValues({})") is None


def test_departure_is_none_when_not_stated():
    interpretation = {**INTERPRETATION, "departure_time": None}
    assert run_js(f"T.formValues({json.dumps(interpretation)}).departureLocal") is None
    assert run_js("T.toLocalInputValue('not a date')") is None


def test_summary_describes_the_request_and_escapes_notes():
    html = run_js(f"T.summaryHtml({json.dumps(INTERPRETATION)})")
    assert "loop from <strong>Braunschweig</strong>" in html
    assert "via Wolfenbüttel" in html
    assert "gravel bike" in html
    assert "about 50 km" in html
    assert "ferries are fine" in html
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html


def test_summary_of_a_point_to_point_ride():
    interpretation = {
        "request": {"origin": "A", "destination": "B", "via": [], "constraints": {}},
        "notes": [],
    }
    html = run_js(f"T.summaryHtml({json.dumps(interpretation)})")
    assert "<strong>A</strong> &rarr; <strong>B</strong>" in html
    assert "<ul" not in html
