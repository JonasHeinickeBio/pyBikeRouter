"""The pure helpers in frontend/coverage.js, run under node."""

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
        f"const C = require({json.dumps(str(FRONTEND / 'coverage.js'))});\n"
        f"process.stdout.write(JSON.stringify(C.{name}("
        f"{', '.join(json.dumps(a) for a in args)})));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def error(**detail):
    base = {
        "segments": ["W5_N50", "E10_N50"],
        "reported_missing": ["W5_N50"],
        "download_base": "https://brouter.de/brouter/segments4/",
        "configured_engines": ["brouter"],
    }
    return {
        "code": "routing_area_not_covered",
        "provider": "brouter",
        "message": "BRouter has no map data for this area (segment W5_N50)",
        "detail": {**base, **detail},
    }


def test_the_coverage_error_is_found_among_other_errors():
    errors = [{"code": "no_route"}, error(), None]
    assert call("find", errors)["provider"] == "brouter"
    assert call("find", [{"code": "no_route"}]) is None
    assert call("find", None) is None


def test_the_download_command_names_every_missing_tile_and_the_official_source():
    command = call("downloadCommand", ["W5_N50", "W5_N45"], "https://brouter.de/brouter/segments4/")
    assert command.splitlines()[0] == "cd docker/brouter"
    assert (
        "curl -fL --create-dirs -o segments/W5_N50.rd5 https://brouter.de/brouter/segments4/W5_N50.rd5"
        in command
    )
    assert command.count("curl") == 2


def test_the_card_puts_both_options_to_the_user_and_says_nothing_happens_by_itself():
    html = call("cardHtml", error(), False)
    assert "Map data missing" in html and "no route could be made" in html
    assert "Nothing is downloaded or changed automatically" in html
    assert "1. Download the missing tile" in html and "100&ndash;200&nbsp;MB" in html
    assert "segments/W5_N50.rd5" in html and 'data-copy="coverage-download"' in html
    assert "2. Use all routing engines" in html and "ROUTING_PROVIDER=all" in html
    assert 'data-copy="coverage-env"' in html
    # The other tile of the trip is mentioned, but not part of the command.
    assert "E10_N50" in html and "segments/E10_N50.rd5" not in html


def test_when_another_engine_answered_the_card_says_the_result_comes_from_it():
    html = call("cardHtml", error(configured_engines=["brouter", "ors"]), True)
    assert "the result below comes from the other engine" in html
    assert "Use all routing engines" not in html  # already several engines
    assert "Several engines are already configured (brouter, ors)" in html


def test_nothing_is_shown_without_a_usable_tile_name():
    assert call("cardHtml", error(segments=[], reported_missing=[]), False) == ""
    # Names that are not tile names never reach a shell command.
    html = call(
        "cardHtml",
        error(segments=["W5_N50; rm -rf /", "../x"], reported_missing=["W5_N50; rm -rf /"]),
        False,
    )
    assert html == ""


def test_a_download_source_that_is_not_https_falls_back_to_the_official_one():
    html = call("cardHtml", error(download_base="http://evil.example/"), False)
    assert "evil.example" not in html and "https://brouter.de/brouter/segments4/" in html
    html = call("cardHtml", error(download_base="javascript:alert(1)//"), False)
    assert "javascript" not in html


def test_text_from_the_error_is_escaped():
    html = call("cardHtml", error(configured_engines=["<img src=x>", "b"]), True)
    assert "<img" not in html
