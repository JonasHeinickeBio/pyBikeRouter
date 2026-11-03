"""The pure helpers in frontend/rider-settings.js, run under node, and how app.js uses them."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "src" / "bike_routing_agent" / "frontend"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

SETTINGS = [
    {"name": "consider_noise", "type": "boolean", "group": "preferences",
     "meaning": "prefer quiet"},
    {"name": "avoid_unsafe", "type": "boolean", "group": "cycle routes", "meaning": "no highways"},
    {"name": "uphillcost", "type": "number", "group": "climbing", "minimum": 0, "maximum": 200,
     "meaning": "cost per metre climbed"},
    {"name": "bikerPower", "type": "number", "group": "rider", "minimum": 20, "maximum": 1000,
     "meaning": "average power in W"},
]  # fmt: skip


def call(name: str, *args):
    script = (
        f"const S = require({json.dumps(str(FRONTEND / 'rider-settings.js'))});\n"
        f"const out = S.{name}({', '.join(json.dumps(a) for a in args)});\n"
        "process.stdout.write(JSON.stringify(out === undefined ? null : out));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_settings_are_grouped_in_a_fixed_order_whatever_the_api_sends():
    shuffled = [SETTINGS[3], SETTINGS[2], SETTINGS[0], SETTINGS[1]]
    got = call("groups", shuffled)
    assert [g["key"] for g in got] == ["preferences", "cycle routes", "climbing", "rider"]
    assert "not the route" in got[-1]["label"]
    assert [i["name"] for i in got[0]["items"]] == ["consider_noise"]


def test_a_group_the_page_does_not_know_still_shows_after_the_known_ones():
    extra = {"name": "x", "type": "boolean", "group": "new group", "meaning": "m"}
    got = call("groups", [extra, SETTINGS[0]])
    assert [g["key"] for g in got] == ["preferences", "new group"]
    assert call("groups", []) == [] and call("groups", None) == []


def test_the_hint_says_what_it_does_and_the_range():
    assert call("hint", SETTINGS[0]) == "prefer quiet (on/off)"
    assert call("hint", SETTINGS[2]) == "cost per metre climbed (0–200)"


@pytest.mark.parametrize(
    ("setting", "raw", "expected"),
    [
        (SETTINGS[0], "on", True),
        (SETTINGS[0], "off", False),
        (SETTINGS[0], "", None),
        (SETTINGS[0], "maybe", None),
        (SETTINGS[2], "120", 120),
        (SETTINGS[2], " 12.5 ", 12.5),
        (SETTINGS[2], "0", 0),
        (SETTINGS[2], "", None),
        (SETTINGS[2], "lots", None),
        (SETTINGS[2], None, None),
    ],
)
def test_one_typed_value_becomes_the_value_the_request_wants_or_nothing(setting, raw, expected):
    assert call("parseValue", setting, raw) == expected


def test_only_what_was_typed_is_sent_and_zero_is_a_value():
    raw = {"consider_noise": "on", "uphillcost": "0", "bikerPower": "", "gone": "5"}
    assert call("readParams", SETTINGS, raw) == {"consider_noise": True, "uphillcost": 0}
    assert call("readParams", SETTINGS, {}) == {}
    assert call("readParams", [], raw) == {}


def test_typed_values_the_new_profile_lacks_are_dropped():
    raw = {"consider_noise": "on", "uphillcost": "90"}
    assert call("keep", raw, SETTINGS[:1]) == {"consider_noise": "on"}
    assert call("keep", raw, []) == {}


def test_the_summary_counts_what_was_changed():
    assert call("summary", {}) == ""
    assert call("summary", {"a": 1, "b": False}) == "2 changed"


# --- the page: wired to the endpoint, and safe


def test_the_page_asks_the_api_and_sends_only_typed_values():
    app = (FRONTEND / "app.js").read_text()
    assert "v1/profiles/settings?bike_type=" in app
    assert "BikeRiderSettings.readParams(state.rider.settings, state.rider.values)" in app
    assert "constraints.profile_params = tuned" in app
    # a slow answer for the previous bike type must not replace the current one
    assert "request !== state.rider.request" in app
    # the new section starts hidden and is rebuilt without innerHTML (the names come from the API)
    html = (FRONTEND / "index.html").read_text()
    assert 'id="tune" class="tune" hidden' in html
    render = app[app.index("function renderRiderSettings") : app.index("function updateRiderCount")]
    assert "innerHTML" not in render and "textContent" in render


def test_resetting_the_form_clears_the_typed_settings():
    app = (FRONTEND / "app.js").read_text()
    reset = app[app.index('els.bikeType.value = "gravel";') :][:400]
    assert "state.rider.values = {}" in reset and "loadRiderSettings()" in reset
