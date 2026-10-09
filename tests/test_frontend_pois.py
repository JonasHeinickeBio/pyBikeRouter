"""The pure helpers in frontend/pois.js, run under node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "src" / "bike_routing_agent" / "frontend"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

CATEGORIES = [
    {"key": "viewpoint", "label": "Viewpoints", "kind": "sight"},
    {"key": "historic", "label": "Castles & historic sites", "kind": "sight"},
    {"key": "water", "label": "Drinking water", "kind": "service"},
]


def run_js(expression: str):
    script = (
        f"const P = require({json.dumps(str(FRONTEND / 'pois.js'))});\n"
        f"const CATS = {json.dumps(CATEGORIES)};\n"
        f"process.stdout.write(JSON.stringify({expression}));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def call(name: str, *args):
    return run_js(f"P.{name}({', '.join(json.dumps(a) for a in args)})")


def poi(**kw):
    return {"id": "node/1", "category": "historic", "kind": "sight", "lon": 10.1, "lat": 47.5, **kw}


def test_fame_is_stated_as_a_fact_and_unknown_is_silent():
    assert run_js("P.fameText(93)") == "Described in 93 languages"
    assert run_js("P.fameText(1)") == "Described in 1 language"
    assert run_js("P.fameText(null)") == ""
    assert run_js("[P.isFamous({fame: 30}), P.isFamous({fame: 29}), P.isFamous({})]") == [
        True,
        False,
        False,
    ]


def test_distance_from_the_route_reads_naturally():
    assert run_js("P.distanceText(10)") == "on the route"
    assert run_js("P.distanceText(347)") == "350 m from the route"
    assert run_js("P.distanceText(1500)") == "1.5 km from the route"
    assert run_js("P.distanceText(null)") == ""


def test_popup_shows_the_facts_and_offers_both_actions_for_a_linked_poi():
    burg = poi(name="Burg", fame=60, distance_from_route_m=420, wikidata="Q1")
    html = run_js(f"P.popupHtml({json.dumps(burg)}, CATS)")
    assert "Burg" in html and "Castles &amp; historic sites" in html
    assert "Described in 60 languages" in html and "420 m from the route" in html
    assert 'data-act="info"' in html and 'data-act="add"' in html


def test_popup_without_links_has_no_read_more_and_falls_back_to_the_category_name():
    html = run_js(f"P.popupHtml({json.dumps(poi(category='viewpoint'))}, CATS)")
    assert "Viewpoints" in html and 'data-act="info"' not in html and 'data-act="add"' in html


def test_popup_escapes_everything_that_came_from_the_map():
    hostile = poi(name="<img src=x onerror=alert(1)>", opening_hours='"><b>')
    html = run_js(f"P.popupHtml({json.dumps(hostile)}, CATS)")
    assert "<img" not in html and "&lt;img" in html and "<b>" not in html


def test_info_html_renders_text_links_and_attribution_and_nothing_executable():
    info = {
        "title": "Burg",
        "description": "Burg in <Bayern>",
        "extract": "Eine <script>alert(1)</script> Burg.",
        "language": "de",
        "thumbnail_url": "https://thumb.wikimedia.org/x.jpg",
        "links": [
            {"label": "Wikipedia (de)", "url": "https://de.wikipedia.org/wiki/Burg"},
            {"label": "evil", "url": "javascript:alert(1)"},
        ],
        "attribution": ["Map data: OpenStreetMap contributors (ODbL)"],
    }
    html = run_js(f"P.infoHtml({json.dumps(info)})")
    assert "<script" not in html and "javascript:" not in html
    assert 'lang="de"' in html and "&lt;script&gt;" in html
    assert (
        'href="https://de.wikipedia.org/wiki/Burg"' in html and 'rel="noopener noreferrer"' in html
    )
    assert (
        'src="https://thumb.wikimedia.org/x.jpg"' in html and "OpenStreetMap contributors" in html
    )


def test_info_html_says_so_when_there_is_nothing_to_show():
    assert "No description available" in run_js("P.infoHtml({links: []})")
    assert run_js("P.infoHtml(null)") == ""


def test_info_query_carries_only_what_the_poi_has():
    full = poi(wikidata="Q4152", wikipedia="de:Schloss", website="https://x.org")
    query = call("infoQuery", full, "de-AT")
    assert "wikidata=Q4152" in query and "osm_id=node%2F1" in query and "lang=de" in query
    assert "website=https%3A%2F%2Fx.org" in query
    bare = run_js(f"P.infoQuery({json.dumps(poi(website='javascript:x'))}, undefined)")
    assert "website" not in bare and "wikidata" not in bare and "lang=en" in bare


def test_default_filter_shows_sights_not_services():
    assert run_js("P.defaultPrefs(CATS)") == {"enabled": True, "keys": ["viewpoint", "historic"]}


def test_filter_is_remembered_and_survives_broken_storage():
    saved = run_js(
        "(() => { const s = {};"
        " const st = {getItem: k => s[k] ?? null, setItem: (k, v) => { s[k] = v; }};"
        " P.savePrefs(st, {enabled: false, keys: ['water', 'bogus']});"
        " return P.loadPrefs(st, CATS); })()"
    )
    assert saved == {"enabled": False, "keys": ["water"]}  # unknown keys dropped
    for storage in (
        "{getItem: () => 'not json'}",
        "{getItem: () => { throw new Error('blocked'); }}",
        "{getItem: () => JSON.stringify({enabled: 'yes'})}",
    ):
        assert run_js(f"P.loadPrefs({storage}, CATS)") == {
            "enabled": True,
            "keys": ["viewpoint", "historic"],
        }
    full = "{setItem: () => { throw new Error('full'); }}"
    save = f"P.savePrefs({full}, {{enabled: true, keys: []}})"
    assert run_js(f"(() => {{ {save}; return 'ok'; }})()") == "ok"


ROUTE = [[10.0, 47.5], [10.1, 47.5], [10.2, 47.5], [10.3, 47.5]]


def test_an_added_poi_goes_between_the_vias_it_lies_between():
    vias = [{"lon": 10.05, "lat": 47.5}, {"lon": 10.25, "lat": 47.5}]
    expr = f"P.insertionIndex({json.dumps(ROUTE)}, {json.dumps(vias)}, {{lon: %s, lat: 47.501}})"
    assert run_js(expr % "10.01") == 0
    assert run_js(expr % "10.15") == 1
    assert run_js(expr % "10.29") == 2


def test_without_a_route_or_with_a_named_via_the_poi_goes_last():
    assert run_js("P.insertionIndex(null, [{lon: 1, lat: 1}], {lon: 0, lat: 0})") == 1
    named = [None, {"lon": 10.25, "lat": 47.5}]
    assert (
        run_js(
            f"P.insertionIndex({json.dumps(ROUTE)}, {json.dumps(named)}, {{lon: 10.01, lat: 47.5}})"
        )
        == 2
    )


def test_thin_bounds_the_request_and_keeps_the_ends():
    coords = [[i / 1000, 47.0] for i in range(5000)]
    thinned = run_js(f"P.thin({json.dumps(coords)}, 100)")
    assert len(thinned) == 100 and thinned[0] == coords[0] and thinned[-1] == coords[-1]
    assert run_js("P.thin([[0, 0], [1, 1]], 100)") == [[0, 0], [1, 1]]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("ok", "Passing the best-known sights: Burg, Aussicht"),
        ("none_found", "planned without stops"),
        ("unavailable", "not available right now"),
        ("unsupported", "not available for loops"),
        ("dropped", "could not reach the sights by bike"),
    ],
)
def test_the_note_about_the_stops_says_what_happened(status, expected):
    stops = [{"name": "Burg"}, {"category": "Aussicht"}]
    assert expected in run_js(f"P.stopsNote({json.dumps(status)}, {json.dumps(stops)})")


def test_no_note_when_nothing_was_asked():
    assert run_js("P.stopsNote(null, null)") == ""
    assert run_js("P.stopsNote('ok', [])") == ""


def test_a_view_wider_than_the_servers_limit_asks_for_a_zoom_instead_of_failing():
    # The view that the deployed app logged as 422: 0.5009 degrees wide at zoom 11.
    assert call("viewTooLarge", -1.50032, 51.66084, -0.99941, 51.83917) is True
    assert call("viewTooLarge", 10.50, 52.25, 10.55, 52.28) is False
    assert call("viewTooLarge", 10.0, 52.0, 10.1, 52.6) is True  # too tall
    assert call("viewTooLarge", 10.0, 52.0, 10.49, 52.1) is True  # inside the margin
    assert call("viewTooLarge", 10.0, 52.0, 10.45, 52.45) is False
    assert call("viewTooLarge", 10.0, 52.0, 10.2, 52.2, 0.1) is True  # another limit
