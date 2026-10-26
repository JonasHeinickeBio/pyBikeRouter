"""frontend/picks.js: the report of a downloaded route for the opt-in pick log, under node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "src" / "bike_routing_agent" / "frontend"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

J = json.dumps


def run_js(body: str):
    script = (
        f"const P = require({json.dumps(str(FRONTEND / 'picks.js'))});\n"
        f"process.stdout.write(JSON.stringify((() => {{ {body} }})()));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


ROUTES = [
    {"metrics": {"distance_m": 10000}, "geometry_geojson": {"type": "LineString"}, "provider": "a"},
    {"metrics": {"distance_m": 12000}, "geometry_geojson": {"type": "LineString"}, "provider": "b"},
    {"metrics": {"distance_m": 10010}, "duplicate_of": "a/x", "provider": "c"},
]


def test_the_report_has_the_numbers_of_the_distinct_routes_and_nothing_else():
    body = run_js(
        f"return P.payload({J(ROUTES)}, 1, 0, 'gravel', [{{metric: 'traffic', weight: 0.7}}]);"
    )
    assert body == {
        "candidates": [{"metrics": {"distance_m": 10000}}, {"metrics": {"distance_m": 12000}}],
        "picked": 1,
        "selected": 0,
        "goals": [{"metric": "traffic", "weight": 0.7}],
        "bike_type": "gravel",
    }


def test_the_positions_count_among_the_distinct_routes_not_the_near_copies():
    routes = [ROUTES[2], ROUTES[0], ROUTES[1]]  # a near-copy first: the others shift down
    body = run_js(f"return P.payload({J(routes)}, 2, 1, '', null);")
    assert (body["picked"], body["selected"]) == (1, 0)
    assert "bike_type" not in body and body["goals"] == []


@pytest.mark.parametrize(
    "call",
    [
        "P.payload(null, 0, 0, '', [])",
        f"P.payload({J(ROUTES[:1])}, 0, 0, '', [])",  # one route is no comparison
        f"P.payload({J(ROUTES)}, 2, 0, '', [])",  # a near-copy nobody was offered
        f"P.payload({J(ROUTES)}, 5, 0, '', [])",
        f"P.payload({J([ROUTES[0]] * 17)}, 0, 1, '', [])",  # more than the server takes
    ],
)
def test_nothing_is_reported_when_there_is_nothing_to_compare(call):
    assert run_js(f"return {call};") is None


def test_goals_that_the_server_would_refuse_are_left_out():
    goals = [
        {"metric": "traffic", "weight": 1},
        {"metric": 3, "weight": 1},
        {"metric": "paved", "weight": 0},
        {"metric": "paved", "weight": 11},
        {"metric": "ascent", "weight": "x"},
        None,
    ]
    body = run_js(f"return P.payload({J(ROUTES)}, 0, 0, '', {J(goals)});")
    assert body["goals"] == [{"metric": "traffic", "weight": 1}]


def test_the_same_pick_has_the_same_key_and_another_route_another_one():
    keys = run_js(
        f"const a = P.payload({J(ROUTES)}, 1, 0, '', []);"
        f"const b = P.payload({J(ROUTES)}, 0, 0, '', []);"
        "return [P.key(a), P.key(a), P.key(b)];"
    )
    assert keys[0] == keys[1] != keys[2]


def test_the_page_loads_the_script_and_reports_only_after_a_download():
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert page.index('src="picks.js"') < page.index('src="app.js"')
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    report = app.split("async function reportPick")[1].split("\n}\n")[0]
    assert "pick_log" in report and "/v1/route/pick" in report and "keepalive" in report
    download = app.split("function downloadActiveCandidate")[1].split("\n}\n")[0]
    assert download.index("saveFile") < download.index("reportPick()")
