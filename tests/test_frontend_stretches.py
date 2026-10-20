"""frontend/stretches.js: colouring the route by surface or traffic, under node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "src" / "bike_routing_agent" / "frontend"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# 11 vertices 0.01 degrees of latitude apart: about 1112 m between neighbours, 11.1 km in all.
LINE = [[10.0, 52.0 + i * 0.01] for i in range(11)]
STEP_M = 1111.95


def run_js(expression: str):
    script = (
        f"const S = require({json.dumps(str(FRONTEND / 'stretches.js'))});\n"
        f"process.stdout.write(JSON.stringify({expression}));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def stretch(a, b, surface="paved", traffic="quiet"):
    return {"from_index": a, "to_index": b, "surface": surface, "traffic": traffic}


def cand(stretches, line=LINE):
    return {"geometry_geojson": {"type": "LineString", "coordinates": line}, "stretches": stretches}


def call(name, candidate, *args):
    rest = "".join(f", {json.dumps(a)}" for a in args)
    return run_js(f"S.{name}({json.dumps(candidate)}{rest})")


MIXED = cand(
    [
        stretch(0, 3, "paved", "quiet"),
        stretch(3, 4, "paved", "busy"),
        stretch(4, 8, "unpaved", "off_road"),
        stretch(8, 10, "cobbles", "unknown"),
    ]
)


def test_modes_offered_are_those_with_something_known():
    assert call("modes", MIXED) == ["surface", "traffic"]
    no_traffic = cand([stretch(0, 5, "paved", "unknown"), stretch(5, 10, "unpaved", "unknown")])
    assert call("modes", no_traffic) == ["surface"]
    nothing = cand([stretch(0, 10, "unknown", "unknown")])
    assert call("modes", nothing) == []  # all untagged: not one grey line pretending to be data


@pytest.mark.parametrize(
    "candidate",
    [
        {},
        {"stretches": [stretch(0, 5)]},  # no geometry
        cand([]),
        cand(None),
        cand([stretch(0, 5)], line=[[10.0, 52.0]]),
        cand([stretch(0, 5)], line=[[10.0, 52.0], ["x", 1]]),
    ],
)
def test_nothing_usable_offers_nothing(candidate):
    assert call("modes", candidate) == []
    assert call("pieces", candidate, "surface") == []
    assert call("legend", candidate, "surface") == []
    assert call("longest", candidate, "surface") is None


def test_pieces_follow_the_geometry_and_carry_their_colour_and_extent():
    pieces = call("pieces", MIXED, "surface")
    assert [(p["key"], p["fromIndex"], p["toIndex"]) for p in pieces] == [
        ("paved", 0, 4),  # the two paved stretches are one piece in this mode
        ("unpaved", 4, 8),
        ("cobbles", 8, 10),
    ]
    assert pieces[0]["latlngs"][0] == [52.0, 10.0] and len(pieces[0]["latlngs"]) == 5  # lat first
    assert pieces[0]["latlngs"][-1] == pieces[1]["latlngs"][0]  # no gap between pieces
    assert pieces[1]["fromM"] == pytest.approx(4 * STEP_M, rel=0.01)
    assert pieces[1]["color"].startswith("#")


def test_the_same_route_cuts_differently_by_traffic():
    pieces = call("pieces", MIXED, "traffic")
    assert [(p["key"], p["fromIndex"], p["toIndex"]) for p in pieces] == [
        ("quiet", 0, 3),
        ("busy", 3, 4),
        ("off_road", 4, 8),
        ("unknown", 8, 10),
    ]


def test_the_legend_adds_up_what_each_class_covers_largest_first():
    legend = call("legend", MIXED, "surface")
    assert [r["key"] for r in legend] == ["paved", "unpaved", "cobbles"]
    assert legend[0]["meters"] == pytest.approx(4 * STEP_M, rel=0.01)
    assert sum(r["share"] for r in legend) == pytest.approx(1.0)
    assert legend[1]["share"] == pytest.approx(0.4, abs=0.01)


def test_a_class_of_a_few_metres_is_not_a_legend_row():
    tiny_line = [[10.0, 52.0 + i * 0.00001] for i in range(3)] + [
        [10.0, 52.0 + i * 0.01] for i in range(1, 9)
    ]
    c = cand([stretch(0, 2, "unpaved"), stretch(2, 10, "paved")], tiny_line)  # the first is ~2 m
    assert [r["key"] for r in call("legend", c, "surface")] == ["paved"]


def test_the_longest_notable_stretch_is_the_unpaved_or_the_busy_one():
    unpaved = call("longest", MIXED, "surface")
    assert unpaved["label"] == "unpaved" and unpaved["meters"] == pytest.approx(
        4 * STEP_M, rel=0.01
    )
    assert unpaved["atM"] == pytest.approx(4 * STEP_M, rel=0.01)
    assert (
        run_js(f"S.longestText({json.dumps(unpaved)})")
        == "Longest unpaved stretch: 4.4 km, from km 4.4."
    )
    busy = call("longest", MIXED, "traffic")
    assert busy["label"] == "busy roads" and busy["meters"] == pytest.approx(STEP_M, rel=0.01)


def test_a_short_notable_stretch_is_not_worth_a_sentence():
    short_line = [[10.0, 52.0 + i * 0.0005] for i in range(11)]  # 55 m steps
    short = cand(
        [stretch(0, 5, "paved"), stretch(5, 7, "unpaved"), stretch(7, 10, "paved")], short_line
    )
    assert call("longest", short, "surface") is None
    assert run_js("S.longestText(null)") == ""


def test_the_longest_is_the_longest_not_the_first():
    c = cand(
        [
            stretch(0, 2, "unpaved"),
            stretch(2, 4, "paved"),
            stretch(4, 9, "unpaved"),
            stretch(9, 10, "paved"),
        ]
    )
    assert call("longest", c, "surface")["atM"] == pytest.approx(4 * STEP_M, rel=0.01)


def test_stretches_that_do_not_fit_the_geometry_are_skipped_not_drawn():
    c = cand(
        [
            stretch(0, 3),
            stretch(2, 5, "unpaved"),  # overlaps the previous one
            {"from_index": 3.5, "to_index": 6, "surface": "unpaved", "traffic": "quiet"},
            stretch(5, 5, "unpaved"),  # empty
            stretch(6, 99, "unpaved"),  # past the end
            {"from_index": "a", "to_index": 8},
            None,
            stretch(3, 7, "unpaved", "busy"),
        ]
    )
    assert [(p["fromIndex"], p["toIndex"]) for p in call("pieces", c, "surface")] == [
        (0, 3),
        (3, 7),
    ]


def test_an_unknown_class_falls_back_to_not_mapped():
    c = cand([stretch(0, 5, "lava", "quiet"), stretch(5, 10, "paved", "quiet")])
    assert call("pieces", c, "surface")[0]["label"] == "Not mapped"


def test_an_unknown_mode_is_empty():
    assert call("pieces", MIXED, "wind") == [] and call("longest", MIXED, "wind") is None


def test_the_legend_html_is_escaped_and_shows_length_and_share():
    row = {"key": "x", "label": "<b>Paved</b>", "color": 'red"><i>', "meters": 4400, "share": 0.4}
    html = run_js(f"S.legendHtml([{json.dumps(row)}])")
    assert "<b>" not in html and "&lt;b&gt;" in html and "<i>" not in html
    assert "4.4 km" in html and "40 %" in html
    assert run_js("S.legendHtml(null)") == ""


def test_each_mode_says_what_its_colours_are_and_are_not():
    modes = run_js("S.MODES")
    assert "no tag" in modes["surface"]["note"]
    assert "not a count of cars" in modes["traffic"]["note"]
    for spec in modes.values():
        assert spec["classes"][-1]["key"] == "unknown"  # the fallback class is last


# ------------------------------------------------------------------------------ page wiring


def test_the_page_has_the_card_and_the_app_draws_and_clears_it():
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert 'id="colours-card"' in page and 'id="colours-modes"' in page
    assert page.index('src="stretches.js"') < page.index('src="app.js"')
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "renderColours(cand);" in app
    assert "state.colourLayer.clearLayers();" in app and "els.coloursCard.hidden = true;" in app
    assert "interactive: false" in app[app.index("function renderColours") :]  # clicks pass through
