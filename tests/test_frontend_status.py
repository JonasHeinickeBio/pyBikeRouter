"""The service status indicator's pure helpers (frontend/status.js), run under node."""

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
        f"const S = require({json.dumps(str(FRONTEND / 'status.js'))});\n"
        f"process.stdout.write(JSON.stringify(S.{name}("
        f"{', '.join(json.dumps(a) for a in args)})));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def component(kind, name, status, detail=None, required=False):
    return {"kind": kind, "name": name, "status": status, "detail": detail, "required": required}


def report(status, *components):
    return {"status": status, "ready": status != "unavailable", "components": list(components)}


ALL_UP = report(
    "ok",
    component("routing", "brouter", "ok", required=True),
    component("geocoder", "nominatim", "ok", required=True),
    component("weather", "weather", "ok"),
    component("pois", "overpass", "ok"),
)
SIGHTS_DOWN = report(
    "degraded",
    component("routing", "brouter", "ok", required=True),
    component("geocoder", "nominatim", "ok", required=True),
    component("weather", "weather", "ok"),
    component("pois", "overpass", "unavailable", "unreachable"),
)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (ALL_UP, "ok"),
        (SIGHTS_DOWN, "degraded"),
        (report("unavailable"), "unavailable"),
        ({"status": "weird"}, None),
        ({}, None),
        (None, None),
    ],
)
def test_the_level_is_the_servers_overall_verdict_or_nothing(given, expected):
    assert call("levelOf", given) == expected


def test_the_summary_names_what_is_down_in_plain_words():
    assert call("summaryText", ALL_UP) == "All services up"
    assert call("summaryText", SIGHTS_DOWN) == "Some services are down: Sights"
    assert call("summaryText", report("unavailable")) == "Cannot plan routes right now"
    assert call("summaryText", None) == "Status unknown"
    two = report(
        "degraded",
        component("weather", "weather", "unavailable"),
        component("pois", "overpass", "degraded", "HTTP 504"),
    )
    assert call("summaryText", two) == "Some services are down: Weather, Sights"


def test_a_service_without_a_health_signal_is_not_a_problem():
    unknown = report("ok", component("routing", "ors", "unknown", "no health signal"))
    assert call("problems", unknown) == []
    assert call("summaryText", unknown) == "All services up"


def test_engines_and_geocoders_carry_their_own_name_other_services_do_not():
    assert call("labelOf", component("routing", "brouter", "ok"), []) == "Route engine (brouter)"
    assert (
        call("labelOf", component("geocoder", "nominatim", "ok"), []) == "Place search (nominatim)"
    )
    assert call("labelOf", component("pois", "overpass", "ok"), []) == "Sights"
    assert call("labelOf", component("weather", "weather", "ok"), []) == "Weather"
    assert call("labelOf", component("artifact_store", "artifact_store", "ok"), []) == "Route files"
    assert call("labelOf", component("mystery", "x", "ok"), []) == "mystery"


def test_the_rows_show_each_services_state_and_the_servers_short_reason():
    html = call("rowsHtml", SIGHTS_DOWN)
    assert html.count("<li") == 4
    assert 'class="svc ok"' in html and 'class="svc unavailable"' in html
    assert "Sights</span>" in html and "down &middot; unreachable" in html
    degraded = report("degraded", component("pois", "overpass", "degraded", "HTTP 504"))
    assert "slow or partly down &middot; HTTP 504" in call("rowsHtml", degraded)
    ok = report("ok", component("weather", "weather", "ok", "stale detail"))
    assert "up</span>" in call("rowsHtml", ok) and "stale detail" not in call("rowsHtml", ok)


def test_an_unknown_state_word_reads_as_no_signal_and_text_is_escaped():
    odd = report("ok", component("pois", '<img src=x onerror="1">', "frobnicated"))
    html = call("rowsHtml", odd)
    assert "no signal" in html and 'class="svc unknown"' in html
    sneaky = report("degraded", component("routing", "<b>x</b>", "unavailable", "<script>"))
    html = call("rowsHtml", sneaky)
    assert "<b>" not in html and "<script>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html


def test_malformed_reports_give_no_rows_and_no_crash():
    for bad in (None, {}, {"components": "nope"}, {"components": [None, {"kind": "x"}]}):
        assert call("rowsHtml", bad) == ""


def test_the_page_polls_readyz_and_shows_the_dot_only_when_the_server_has_it():
    html = (FRONTEND / "index.html").read_text()
    assert 'id="service-status"' in html and 'src="status.js"' in html
    assert html.index('src="chat.js"') < html.index('src="status.js"') < html.index('src="app.js"')
    app = (FRONTEND / "app.js").read_text()
    body = app[app.index("async function refreshStatus") :]
    body = body[: body.index("\n}\n")]
    assert "/readyz" in body and "resp.status === 404" in body  # an older server: no dot
    assert "BikeStatus.levelOf(report)" in body and "unreachable" in body
    assert "visibilityState" in app and "STATUS_POLL_MS" in app  # polled, but not in a hidden tab
