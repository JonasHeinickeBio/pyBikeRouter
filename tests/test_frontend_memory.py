"""frontend/memory.js: the opt-in memory of the plan form, under node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "src" / "bike_routing_agent" / "frontend"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

STORAGE = """const store = new Map();
const storage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => { store.set(k, String(v)); },
  removeItem: (k) => { store.delete(k); },
};
"""


def run_js(body: str):
    script = (
        f"const M = require({json.dumps(str(FRONTEND / 'memory.js'))});\n{STORAGE}\n"
        f"process.stdout.write(JSON.stringify((() => {{ {body} }})()));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


FORM = {
    "bike_type": "gravel",
    "prefer_surfaces": ["gravel", "compacted"],
    "avoid_surfaces": ["masonry"],
    "avoid_high_traffic_roads": True,
    "avoid_ferries": False,
    "target_distance_km": 60,
    "max_distance_km": None,
    "max_ascent_m": 700,
    "origin": "Braunschweig, Germany",
}
J = json.dumps


def test_it_is_off_and_empty_by_default():
    state = run_js("return M.load(storage);")
    assert state["enabled"] is False and state["values"] == {}
    assert run_js("return M.recall(M.emptyState());") == {}
    assert (
        run_js(f"const s = M.remember(M.emptyState(), {J(FORM)}); return s.values;") == {}
    )  # off: nothing


def test_switched_on_it_keeps_the_riders_habits_but_not_the_trip_of_the_day():
    state = run_js(f"let s = M.setEnabled(M.emptyState(), true); return M.remember(s, {J(FORM)});")
    assert state["keep"] == {
        "bike": True,
        "surfaces": True,
        "roads": True,
        "limits": False,
        "start": False,
    }
    assert state["values"] == {
        "bike_type": "gravel",
        "prefer_surfaces": ["gravel", "compacted"],
        "avoid_surfaces": ["masonry"],
        "avoid_high_traffic_roads": True,
        "avoid_ferries": False,
    }  # no distance limits, no start place unless asked


def test_each_kind_can_be_ticked_and_then_takes_what_the_form_holds():
    got = run_js(
        f"""let s = M.setEnabled(M.emptyState(), true);
        s = M.remember(s, {J(FORM)});
        s = M.setKept(s, 'limits', true); s = M.setKept(s, 'start', true);
        s = M.remember(s, {J(FORM)});
        return s;"""
    )
    assert got["values"]["target_distance_km"] == 60 and got["values"]["max_ascent_m"] == 700
    assert got["values"]["origin"] == "Braunschweig, Germany"
    assert "max_distance_km" not in got["values"]  # empty fields are not remembered


def test_unticking_a_kind_forgets_what_it_held_at_once():
    got = run_js(
        f"""let s = M.setEnabled(M.emptyState(), true);
        s = M.remember(s, {J(FORM)});
        return M.setKept(s, 'bike', false);"""
    )
    assert "bike_type" not in got["values"] and "prefer_surfaces" in got["values"]


def test_switching_off_forgets_the_values():
    got = run_js(
        f"""let s = M.setEnabled(M.emptyState(), true);
        s = M.remember(s, {J(FORM)});
        return M.setEnabled(s, false);"""
    )
    assert got["enabled"] is False and got["values"] == {}
    assert run_js("return M.recall(M.setEnabled(M.emptyState(), false));") == {}


def test_what_is_stored_comes_back_and_only_what_is_ticked_is_recalled():
    got = run_js(
        f"""let s = M.setEnabled(M.emptyState(), true);
        s = M.remember(s, {J(FORM)});
        M.save(storage, s);
        const back = M.load(storage);
        return {{enabled: back.enabled, recalled: M.recall(back), text: M.describe(back)}};"""
    )
    assert got["enabled"] is True and got["recalled"]["bike_type"] == "gravel"
    assert got["text"] == "Remembering: bike type, surface preferences, busy roads and ferries."


def test_a_stored_value_is_read_like_any_text_from_outside():
    hostile = {
        "enabled": True,
        "keep": {
            "bike": True,
            "surfaces": True,
            "roads": True,
            "limits": True,
            "start": True,
            "evil": True,
        },
        "values": {
            "bike_type": "tank",
            "prefer_surfaces": ["ok", 5, "", "x" * 100, "<script>"],
            "avoid_high_traffic_roads": "yes",
            "avoid_ferries": False,
            "target_distance_km": -5,
            "max_distance_km": "far",
            "max_ascent_m": 99999,
            "origin": "x" * 300,
            "unknown_field": "<script>",
        },
    }
    got = run_js(f"storage.setItem(M.KEY, {J(J(hostile))}); return M.recall(M.load(storage));")
    assert got == {"prefer_surfaces": ["ok", "<script>"], "avoid_ferries": False}


def test_a_value_of_a_kind_that_is_not_ticked_is_dropped_even_if_it_is_stored():
    stored = {
        "enabled": True,
        "keep": {"bike": False},
        "values": {"bike_type": "road", "avoid_ferries": True},
    }
    got = run_js(f"storage.setItem(M.KEY, {J(J(stored))}); return M.recall(M.load(storage));")
    assert got == {"avoid_ferries": True}


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        "[]",
        "5",
        '{"enabled": "true"}',
        '{"enabled": false, "values": {"bike_type": "road"}}',
        "null",
    ],
)
def test_unreadable_or_off_storage_is_the_empty_state(raw):
    got = run_js(
        f"storage.setItem(M.KEY, {J(raw)}); const s = M.load(storage);"
        " return [s.enabled, s.values];"
    )
    assert got == [False, {}]


def test_a_browser_that_will_not_store_is_not_a_crash():
    got = run_js(
        """const no = () => { throw new Error('no'); };
        const blocked = { getItem: no, setItem: no, removeItem: no };
        return [M.load(blocked).enabled, M.save(blocked, M.emptyState()), M.erase(blocked)];"""
    )
    assert got == [False, False, False]


def test_erase_removes_everything_it_stored():
    got = run_js(
        f"""let s = M.setEnabled(M.emptyState(), true); s = M.remember(s, {J(FORM)});
        M.save(storage, s); const before = storage.getItem(M.KEY) !== null;
        M.erase(storage); return [before, storage.getItem(M.KEY)];"""
    )
    assert got == [True, None]


def test_the_description_says_what_is_going_on():
    assert run_js("return M.describe(M.emptyState());") == "Off: nothing is remembered."
    on = run_js("return M.describe(M.setEnabled(M.emptyState(), true));")
    assert on.startswith("On: nothing stored yet")


def test_an_unknown_kind_is_ignored():
    got = run_js(
        "const s = M.setEnabled(M.emptyState(), true);"
        " return JSON.stringify(M.setKept(s, 'nope', true)) === JSON.stringify(s);"
    )
    assert got is True


# ------------------------------------------------------------------------------ page wiring


def test_the_page_has_the_group_loads_the_script_and_wires_it_before_the_link():
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert 'id="memory-settings"' in page and 'id="memory-enabled"' in page
    assert "Off until you switch it on" in page
    assert page.index('src="memory.js"') < page.index('src="app.js"')
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert app.index("  initMemory();") < app.index("  loadSharedPlan();")
    # a shared link is never mixed with what the browser remembers
    assert "!window.location.hash.startsWith(`#${BikeLink.KEY}=`)" in app
    # it is saved as the form changes and after a plan that worked, and only while it is on
    assert "rememberNow(); // a plan that worked" in app
    # ... but a shared link's trip is not remembered until the form is edited (found by trying it)
    assert "if (!state.memoryHoldBack) rememberNow();" in app
    assert (
        "state.memoryHoldBack = true; // somebody else's trip"
        in app[app.index("function loadSharedPlan") :]
    )
    assert "state.memoryHoldBack = false;" in app[app.index("function initMemory") :]
    assert "if (!state.memory.enabled) return;" in app


def test_a_refused_save_stays_visible_and_a_blocked_storage_lookup_cannot_stop_startup():
    """Both found in review: the failure text was overwritten by the next status update, and merely
    looking at window.localStorage can throw."""
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "state.memoryStorageFailed = !BikeMemory.save(memoryStorage(), state.memory);" in app
    status = app[app.index("function memoryStatusText") : app.index("function renderMemory")]
    assert "state.memoryStorageFailed" in status and "would not store it" in status
    # every status update reads it (no caller sets the status from describe() alone any more)
    assert app.count("els.memoryStatus.textContent = memoryStatusText();") >= 2
    assert "els.memoryStatus.textContent = BikeMemory.describe(" not in app
    # no direct window.localStorage in the memory code: it all goes through the guard
    memory = app[
        app.index("function formForMemory") : app.index(
            "/* --------------------------- settings panel"
        )
    ]
    code = "\n".join(line for line in memory.splitlines() if not line.lstrip().startswith("//"))
    assert "window.localStorage" not in code.replace(
        "return window.localStorage || BLOCKED_STORAGE;", ""
    )
    guard = memory[
        memory.index("function memoryStorage") : memory.index("/** What the settings panel")
    ]
    assert "try {" in guard and "catch {" in guard and "BLOCKED_STORAGE" in guard


def test_hidden_memory_controls_are_really_hidden():
    """.check-grid is display:flex, which beats the browser's own [hidden] (found in review)."""
    css = (FRONTEND / "styles.css").read_text(encoding="utf-8")
    assert ".check-grid[hidden]" in css and ".settings-group button[hidden]" in css
