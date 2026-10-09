"""The pure helpers in frontend/chat.js, run under node."""

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
        f"const C = require({json.dumps(str(FRONTEND / 'chat.js'))});\n"
        f"process.stdout.write(JSON.stringify(C.{name}("
        f"{', '.join(json.dumps(a) for a in args)})));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_messages_are_escaped_and_keep_their_line_breaks():
    html = call("textHtml", 'Route <b>ready</b> & "nice"\nsecond line')
    assert "<b>" not in html and "&lt;b&gt;ready&lt;/b&gt; &amp; &quot;nice&quot;" in html
    assert "<br>second line" in html


def test_only_route_files_become_links():
    sid = "a" * 32
    html = call("textHtml", f"GPX: /v1/routes/{sid}.gpx and /v1/history/plans/x and /etc/passwd")
    assert f'<a href="/v1/routes/{sid}.gpx" download>{sid}.gpx</a>' in html
    assert html.count("<a ") == 1  # nothing else is linked
    assert "<a " not in call("textHtml", "javascript:alert(1)")
    # A look-alike with the wrong shape stays text.
    assert "<a " not in call("textHtml", "/v1/routes/../../secret.gpx")


def test_a_bubble_says_who_spoke():
    bot = call("bubbleHtml", "bot", "hello")
    assert 'class="chat-msg bot"' in bot and ">Bot<" in bot
    user = call("bubbleHtml", "user", "<script>x</script>")
    assert 'class="chat-msg user"' in user and ">You<" in user and "<script>" not in user
    assert 'class="chat-msg bot"' in call("bubbleHtml", "anything else", "x")  # unknown = bot


def test_quick_replies_become_buttons_that_carry_their_own_text():
    html = call("chipsHtml", ["Alternatives", 'Say "hi" <now>', "", None, 7, "x" * 501])
    assert html.count("<button") == 2
    assert 'data-say="Alternatives"' in html
    assert 'data-say="Say &quot;hi&quot; &lt;now&gt;"' in html and "<now>" not in html
    assert call("chipsHtml", None) == ""
    assert call("chipsHtml", [f"s{i}" for i in range(12)]).count("<button") == 8


def test_outgoing_messages_are_trimmed_clipped_and_never_empty():
    assert call("outgoing", "  hello  ") == "hello"
    assert call("outgoing", "   ") is None and call("outgoing", None) is None
    assert len(call("outgoing", "x" * 900)) == 500


def test_the_focused_alternative_is_found_by_rank_not_by_position():
    plan = {"candidates": [{"rank": 2}, {"rank": 1}, {"rank": 3}]}
    assert call("candidateIndex", plan, 1) == 1
    assert call("candidateIndex", plan, 3) == 2
    assert call("candidateIndex", plan, 9) == -1
    assert call("candidateIndex", plan, None) == -1
    assert call("candidateIndex", None, 1) == -1


def test_failures_have_plain_wording():
    assert "switched off" in call("failureText", 503)
    assert "too long or empty" in call("failureText", 422)
    assert "HTTP 500" in call("failureText", 500)
    assert "could not be reached" in call("failureText", 0)


def test_the_page_ships_the_chat_panel_hidden_until_the_server_has_a_chat():
    html = (FRONTEND / "index.html").read_text()
    panel = html.split('id="chat-panel"')[1][:80]
    assert "hidden" in panel
    assert 'src="chat.js"' in html and 'id="chat-form"' in html
    assert 'role="log"' in html and 'aria-live="polite"' in html
    app = (FRONTEND / "app.js").read_text()
    assert "v1/chat" in app and "caps.chat" in app  # shown only when capabilities say so
