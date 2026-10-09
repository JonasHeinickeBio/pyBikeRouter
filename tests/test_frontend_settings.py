"""The pure helpers in frontend/settings.js, run under node."""

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
        f"const S = require({json.dumps(str(FRONTEND / 'settings.js'))});\n"
        f"process.stdout.write(JSON.stringify(S.{name}("
        f"{', '.join(json.dumps(a) for a in args)})));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_attributes_follow_the_open_state():
    assert call("view", True) == {"expanded": "true", "hidden": "false", "label": "Close settings"}
    assert call("view", False) == {"expanded": "false", "hidden": "true", "label": "Open settings"}
    assert call("view", None)["hidden"] == "true"


def test_only_escape_closes_and_only_when_open():
    assert call("closesOnKey", "Escape", True) is True
    assert call("closesOnKey", "Esc", True) is True  # older engines
    assert call("closesOnKey", "Escape", False) is False
    for key in ("Enter", "Tab", "q", " "):
        assert call("closesOnKey", key, True) is False


def test_the_panel_says_when_there_is_nothing_to_configure():
    assert call("isEmpty", [True, True]) is True
    assert call("isEmpty", [True, False]) is False
    assert call("isEmpty", []) is True


def test_the_page_ships_a_hidden_settings_panel_with_a_gear_button():
    html = (FRONTEND / "index.html").read_text()
    # Hidden and unreachable by default; opened by the gear button only.
    assert 'id="settings-panel"' in html and "inert" in html.split('id="settings-panel"')[1][:200]
    assert 'aria-hidden="true"' in html.split('id="settings-panel"')[1][:200]
    assert 'id="settings-toggle"' in html and 'aria-expanded="false"' in html
    css = (FRONTEND / "styles.css").read_text()
    panel_rule = css.split(".settings-panel {")[1].split("}")[0]
    assert "translateX(105%)" in panel_rule and "visibility: hidden" in panel_rule
    assert ".settings-panel.open" in css
    assert "prefers-reduced-motion" in css.split(".settings-panel.open")[1]
