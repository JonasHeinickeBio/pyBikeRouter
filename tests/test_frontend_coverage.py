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


def test_without_server_support_the_card_gives_commands_to_copy():
    html = call("cardHtml", error(), False)
    assert "Map data missing" in html and "no route could be made" in html
    assert "Nothing is downloaded or changed until you choose" in html
    assert "1. Download the missing tile" in html and "100&ndash;200&nbsp;MB" in html
    assert "segments/W5_N50.rd5" in html and 'data-copy="coverage-download"' in html
    assert "BROUTER_SEGMENTS_DIR" in html  # how to get the button
    assert "2. Use openrouteservice instead" in html and "ROUTING_PROVIDER=all" in html
    assert 'data-copy="coverage-env"' in html
    assert "data-action" not in html  # nothing the server cannot do is offered as a button
    # The other tile of the trip is mentioned, but not part of the command.
    assert "E10_N50" in html and "segments/E10_N50.rd5" not in html


CTX = {
    "downloads": True,
    "engines": ["brouter", "ors"],
    "info": {"W5_N50": {"name": "W5_N50", "present": False, "size_bytes": 139294448}},
}


def test_with_server_support_the_card_offers_real_buttons_and_says_how_big_the_download_is():
    html = call("cardHtml", error(), False, CTX)
    assert 'data-action="download" data-segments="W5_N50"' in html
    assert "Download W5_N50 (139 MB) now" in html  # 139294448 bytes, as the source reports it
    assert 'id="coverage-progress"' in html
    assert "Or run it yourself" in html and "segments/W5_N50.rd5" in html  # still available
    assert 'data-action="use-engine" data-engine="ors"' in html
    assert "Plan this trip with openrouteservice" in html and "This trip only" in html
    assert "ROUTING_PROVIDER=all" in html  # the permanent way, tucked into the details


def test_the_size_is_left_out_when_the_source_could_not_say():
    ctx = {**CTX, "info": {"W5_N50": {"present": False, "size_bytes": None}}}
    html = call("cardHtml", error(), False, ctx)
    assert "Download W5_N50 now" in html and "MB) now" not in html


def test_a_tile_already_on_disk_hints_at_a_restart():
    ctx = {**CTX, "info": {"W5_N50": {"present": True, "size_bytes": 1}}}
    assert "already on disk" in call("cardHtml", error(), False, ctx)


def test_the_engine_button_is_only_offered_when_the_server_can_use_that_engine():
    assert "use-engine" not in call("cardHtml", error(), False, {**CTX, "engines": ["brouter"]})
    # ORS already in use: nothing to switch to.
    assert "use-engine" not in call(
        "cardHtml", error(configured_engines=["brouter", "ors"]), True, CTX
    )


def test_progress_is_shown_per_tile_and_failures_are_visible():
    job = {
        "state": "running",
        "segments": [
            {"name": "W5_N50", "state": "downloading", "bytes": 52428800, "total_bytes": 139294448},
            {"name": "E10_N45", "state": "pending", "bytes": 0, "total_bytes": None},
            {"name": "E10_N50", "state": "present", "bytes": 0, "total_bytes": None},
        ],
    }
    html = call("progressHtml", job)
    assert "52 of 139 MB (38%)" in html and "waiting" in html and "already on disk" in html
    failed = {
        "state": "failed",
        "error": "disk full",
        "segments": [{"name": "W5_N50", "state": "failed", "error": "<b>x</b>", "bytes": 0}],
    }
    out = call("progressHtml", failed)
    assert "failed: &lt;b&gt;x&lt;/b&gt;" in out and "<b>" not in out and "disk full" in out
    assert call("progressHtml", None) == ""
    done = {
        "state": "done",
        "segments": [{"name": "W5_N50", "state": "done", "bytes": 1, "total_bytes": 100000000}],
    }
    assert "done (100 MB)" in call("progressHtml", done)


def test_when_another_engine_answered_the_card_says_the_result_comes_from_it():
    html = call("cardHtml", error(configured_engines=["brouter", "ors"]), True)
    assert "the result below comes from the other engine" in html
    assert "Use openrouteservice instead" not in html  # already several engines
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
