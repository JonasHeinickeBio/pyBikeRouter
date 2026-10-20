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
    # One generation counter for form, text and chat plans: an older response never replaces a
    # newer route on the map.
    assert app.count("++state.planGeneration") == 3  # form, text and optimiser plans
    # A stale form/text response is dropped (2); a chat plan is held back only while a newer form
    # request runs and shown after all if that request fails.
    assert app.count("if (generation !== state.planGeneration) return;") == 4  # + optimiser, settle
    assert "generation !== state.planGeneration; // the form asked" in app
    assert "state.deferredChat = { plan: data.plan" in app and "drawChatPlan(held.plan" in app


# ------------------------------------------------------- finding a conversation again


def run_storage(script: str):
    """Run `script` with a fake localStorage `storage` (a Map) and print its JSON result."""
    full = (
        f"const C = require({json.dumps(str(FRONTEND / 'chat.js'))});\n"
        "const map = new Map();\n"
        "const storage = { getItem: (k) => (map.has(k) ? map.get(k) : null),"
        " setItem: (k, v) => map.set(k, String(v)), removeItem: (k) => map.delete(k) };\n"
        f"process.stdout.write(JSON.stringify({script}));"
    )
    result = subprocess.run([NODE, "-e", full], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_session_id_is_remembered_and_found_again():
    sid = "ab" * 16
    assert run_storage(f"(C.rememberSession(storage, '{sid}'), C.storedSession(storage))") == sid
    assert run_storage("C.storedSession(storage)") is None  # nothing stored yet


def test_remembering_nothing_forgets_the_session():
    sid = "cd" * 16
    script = (
        f"(C.rememberSession(storage, '{sid}'), C.rememberSession(storage, null),"
        " C.storedSession(storage))"
    )
    assert run_storage(script) is None


@pytest.mark.parametrize("bad", ["", "nope", "A" * 32, "a" * 31, "a" * 33, "../" + "a" * 29])
def test_a_malformed_session_id_is_neither_stored_nor_trusted(bad):
    assert (
        run_storage(f"(C.rememberSession(storage, {json.dumps(bad)}), C.storedSession(storage))")
        is None
    )
    # ... and one that somehow got into storage is ignored on the way out.
    assert (
        run_storage(
            f"(storage.setItem('pybikerouter.chat', {json.dumps(bad)}), C.storedSession(storage))"
        )
        is None
    )


def test_blocked_storage_neither_fails_nor_remembers():
    script = (
        "(() => { const blocked = { getItem() { throw new Error('denied'); },"
        " setItem() { throw new Error('denied'); }, removeItem() { throw new Error('denied'); } };"
        " C.rememberSession(blocked, 'a'.repeat(32)); C.rememberSession(blocked, null);"
        " return C.storedSession(blocked); })()"
    )
    assert run_storage(script) is None


def test_only_messages_with_a_role_and_text_are_restored():
    history = {
        "messages": [
            {"role": "user", "text": "hi"},
            {"role": "bot", "text": "  "},  # blank: nothing to show
            {"role": "system", "text": "x"},  # not a speaker of this chat
            {"role": "bot", "text": 5},  # not text
            None,
            {"role": "bot", "text": "Here is your route\nGPX"},
        ]
    }
    assert call("historyMessages", history) == [
        {"role": "user", "text": "hi"},
        {"role": "bot", "text": "Here is your route\nGPX"},
    ]
    assert call("historyMessages", None) == [] and call("historyMessages", {}) == []


def test_a_chat_route_fills_the_plan_form_with_the_request_it_came_from():
    app = (FRONTEND / "app.js").read_text()
    draw = app[app.index("function drawChatPlan") :]
    draw = draw[: draw.index("\n}\n")]
    assert (
        "if (request) applyInterpretation({ request, departure_time: request.departure_time })"
        in draw
    )
    # every way a chat plan reaches the map hands its request on: a live turn, a held-back one
    # that is shown after a failed form request, and a restored conversation
    assert "drawChatPlan(data.plan, data.focus_rank, secs, data.request)" in app
    assert "drawChatPlan(held.plan, held.focusRank, held.secs, held.request)" in app
    assert "drawChatPlan(history.plan, history.focus_rank, null, history.request)" in app


# ------------------------------------------------------------ earlier conversations


def A(n: int) -> str:
    return f"{n:032x}"


def test_the_conversations_of_this_browser_are_remembered_newest_first_without_duplicates():
    script = (
        f"(C.rememberSession(storage, '{A(1)}'), C.rememberSession(storage, '{A(2)}'),"
        f" C.rememberSession(storage, '{A(1)}'), C.knownSessions(storage))"
    )
    assert run_storage(script) == [A(1), A(2)]  # 1 was used again, so it is the newest


def test_starting_a_new_chat_forgets_the_current_one_but_keeps_it_in_the_list():
    script = (
        f"(C.rememberSession(storage, '{A(1)}'), C.rememberSession(storage, null),"
        " [C.storedSession(storage), C.knownSessions(storage)])"
    )
    assert run_storage(script) == [None, [A(1)]]


def test_the_list_is_bounded_and_ignores_garbage():
    many = ", ".join(f"C.rememberSession(storage, '{A(n)}')" for n in range(1, 40))
    got = run_storage(f"({many}, C.knownSessions(storage))")
    assert len(got) == 30 and got[0] == A(39)  # the server takes at most 30 ids per request
    junk = json.dumps(json.dumps(["nope", A(5), 7, None, A(5), "../" + "a" * 29]))
    assert run_storage(
        f"(storage.setItem('pybikerouter.chats', {junk}), C.knownSessions(storage))"
    ) == [A(5)]
    assert (
        run_storage("(storage.setItem('pybikerouter.chats', 'not json'), C.knownSessions(storage))")
        == []
    )


def test_forgetting_and_keeping_ids_edits_the_list():
    setup = ", ".join(f"C.rememberSession(storage, '{A(n)}')" for n in (1, 2, 3))
    assert run_storage(
        f"({setup}, C.forgetKnown(storage, '{A(2)}'), C.knownSessions(storage))"
    ) == [A(3), A(1)]
    # what the server still knows, in the order it gave (not the order stored)
    keep = f"C.keepKnown(storage, ['{A(1)}', 'bad', '{A(3)}'])"
    assert run_storage(f"({setup}, {keep}, C.knownSessions(storage))") == [A(1), A(3)]


def test_blocked_storage_does_not_break_the_conversation_list():
    script = (
        "(() => { const blocked = { getItem() { throw new Error('x'); },"
        " setItem() { throw new Error('x'); }, removeItem() { throw new Error('x'); } };"
        f" C.rememberSession(blocked, '{A(1)}'); C.forgetKnown(blocked, '{A(1)}');"
        " return C.knownSessions(blocked); })()"
    )
    assert run_storage(script) == []


@pytest.mark.parametrize(
    "then, now, expected",
    [
        (1000, 1030, "just now"),
        (1000, 1000 + 5 * 60, "5 min ago"),
        (1000, 1000 + 3 * 3600, "3 h ago"),
        (1000, 1000 + 30 * 3600, "yesterday"),
        (1000, 1000 + 4 * 86400, "4 days ago"),
        (0, 20 * 86400, "1970-01-01"),
        (1000, 900, "just now"),  # a clock a little behind is not "in the future"
        (None, 5, ""),
    ],
)
def test_relative_time(then, now, expected):
    assert call("relativeTime", then, now) == expected


def test_the_list_shows_title_age_and_message_count_and_marks_the_current_one():
    summaries = [
        {"session_id": A(1), "title": "from A to B", "updated": 1000, "messages": 4},
        {"session_id": A(2), "title": "40 km loop", "updated": 900, "messages": 2},
    ]
    html = call("conversationsHtml", summaries, 1000 + 300, A(1))
    assert (
        html.count("<li") == 2 and f'data-open="{A(1)}"' in html and f'data-delete="{A(2)}"' in html
    )
    assert "5 min ago" in html and "4 messages" in html and "from A to B" in html
    assert html.count("chat-earlier-item current") == 1 and 'aria-current="true"' in html
    assert call("conversationsHtml", [], 1, None) == ""


def test_a_title_is_escaped_and_a_malformed_entry_is_skipped():
    summaries = [
        {
            "session_id": A(1),
            "title": '<img src=x onerror="alert(1)">',
            "updated": 1,
            "messages": 1,
        },
        {"session_id": "not-an-id", "title": "x", "updated": 1, "messages": 1},
        {"session_id": A(3), "title": "", "updated": 1, "messages": "many"},
        None,
    ]
    html = call("conversationsHtml", summaries, 2, None)
    assert "<img" not in html and "&lt;img" in html
    assert "not-an-id" not in html and html.count("<li") == 2
    assert ">Conversation<" in html and "0 messages" in html  # a missing title / count


def test_the_panel_has_new_chat_and_an_earlier_conversations_list_wired_up():
    html = (FRONTEND / "index.html").read_text()
    assert (
        'id="chat-new"' in html and 'id="chat-earlier-box"' in html and 'id="chat-earlier"' in html
    )
    app = (FRONTEND / "app.js").read_text()
    assert 'els.chatNew.addEventListener("click", newChat)' in app
    assert "/v1/chat/summaries" in app and 'method: "DELETE"' in app
    assert "window.confirm(" in app  # a delete asks first
    # opening one replaces the conversation and the map; a reopened page only fills empty ones
    assert "loadChat(id, true)" in app and "loadChat(id, false)" in app
    # a conversation the server no longer knows is dropped from the list and from "current"
    load = app[app.index("async function loadChat") :]
    load = load[: load.index("\n}\n")]
    assert "forgetKnown(" in load and "rememberSession(window.localStorage, null)" in load
