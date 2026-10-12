"""Traffic and surface as facts: one wording for the chat, the CLI and (facts.js) the web form."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bike_routing_agent.route_facts import (
    busy_share,
    facts_line,
    facts_short,
    surface_shares,
    surface_text,
    traffic_text,
)

BRouter = {
    "main_road_share": 0.19,
    "engine_surface_shares": {"paved": 0.71, "unpaved": 0.24, "cobbles": 0.0, "unknown": 0.05},
}


def test_surface_comes_from_the_engines_tags_largest_first_and_never_counts_unknown_as_paved():
    assert surface_text(BRouter) == "71 % paved, 24 % unpaved, 5 % unknown"
    assert list(surface_shares(BRouter)) == ["paved", "unpaved", "unknown"]  # zero share dropped


def test_without_engine_tags_the_enrichment_is_used_with_its_unknown_part():
    metrics = {"surface_coverage": {"asphalt": 0.5, "gravel": 0.2}, "unknown_surface_fraction": 0.3}
    assert surface_text(metrics) == "50 % asphalt, 30 % unknown, 20 % gravel"
    assert surface_text({}) is None and surface_text({"surface_coverage": {}}) is None


def test_traffic_is_the_main_road_share_and_absent_data_is_not_zero():
    assert traffic_text(BRouter) == "19 % on main roads without a bike lane"
    assert traffic_text({"main_road_share": 0.0}) == "0 % on main roads without a bike lane"
    assert traffic_text({}) is None  # an engine without tags: unknown, not "no traffic"


BANDS = {**BRouter, "traffic_shares": {"busy": 0.27, "some": 0.54, "quiet": 0.12, "off_road": 0.07}}


def test_traffic_is_reported_by_band_when_the_engine_gave_enough_tags():
    assert traffic_text(BANDS) == (
        "27 % busy roads, 54 % some traffic, 12 % quiet streets, 7 % paths and tracks"
    )
    assert busy_share(BANDS) == 0.27
    assert facts_short(BANDS) == "27 % busy roads, 71 % paved"
    assert facts_line(BANDS).startswith(
        "Surface: 71 % paved, 24 % unpaved, 5 % unknown. Traffic (estimate): 27 %"
    )
    # A band under half a percent is left out rather than shown as "0 %".
    tiny = {"traffic_shares": {"busy": 0.001, "some": 0.0, "quiet": 0.2, "off_road": 0.799}}
    assert traffic_text(tiny) == "20 % quiet streets, 80 % paths and tracks"
    assert busy_share(tiny) == 0.001
    # Without the estimate the older main-road number is used, as before.
    assert busy_share(BRouter) == 0.19 and busy_share({}) is None


def test_the_sentence_says_when_the_engine_gave_nothing():
    assert facts_line(BRouter) == (
        "Surface: 71 % paved, 24 % unpaved, 5 % unknown. "
        "Traffic: 19 % on main roads without a bike lane."
    )
    assert facts_line({}) == (
        "Surface: the engine gave no surface data. Traffic: the engine gave no road data."
    )
    assert facts_short(BRouter) == "19 % main roads, 71 % paved"
    assert facts_short({}) == ""


# --- the web form's facts.js words the same facts the same way -----------------------------

NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "src" / "bike_routing_agent" / "frontend"


def js(name: str, *args):
    script = (
        f"const F = require({json.dumps(str(FRONTEND / 'facts.js'))});\n"
        f"process.stdout.write(JSON.stringify(F.{name}("
        f"{', '.join(json.dumps(a) for a in args)})));"
    )
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize(
    "metrics",
    [
        BRouter,
        BANDS,
        {"traffic_shares": {"busy": 0.001, "some": 0.0, "quiet": 0.2, "off_road": 0.799}},
        {},
        {"main_road_share": 0.0},
        {"surface_coverage": {"asphalt": 0.5, "gravel": 0.2}, "unknown_surface_fraction": 0.3},
        {"main_road_share": 0.456, "engine_surface_shares": {"unpaved": 0.9, "paved": 0.1}},
    ],
)
def test_facts_js_says_what_route_facts_py_says(metrics):
    assert js("surfaceText", metrics) == surface_text(metrics)
    assert js("trafficText", metrics) == traffic_text(metrics)
    paved = surface_shares(metrics).get("paved")
    assert js("pavedShare", metrics) == paved
    assert js("busyShare", metrics) == busy_share(metrics)
