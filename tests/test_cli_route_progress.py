"""``route plan``: progress while it runs, ``--trace``, ``--quiet``."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from bike_routing_agent.cli.commands import route as route_cmd
from bike_routing_agent.cli.main import build_parser
from bike_routing_agent.cli.render import format_ms, plan_text, trace_lines

TRACE = [
    {"node": "parse_request", "ms": 0.4, "status": "in_progress", "ok": True},
    {"node": "route_with_provider", "ms": 8200.0, "status": "in_progress", "ok": True},
    {"node": "explain_and_export", "ms": 40.0, "status": "ready", "ok": True},
]


READY_STATE: dict[str, Any] = {
    "status": "ready",
    "explanation": "direct route",
    "errors": [],
    "selected_candidate": {"distance_m": 4200.0, "duration_s": 900.0},
}


class FakeGraph:
    def __init__(self, final: dict[str, Any]) -> None:
        self.final = final

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        return self.final


@pytest.fixture(autouse=True)
def _no_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(route_cmd, "_default_history_factory", lambda: None)


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


class StreamingGraph:
    """A graph that can stream: one update per step, then the final state."""

    def __init__(self, final: dict[str, Any]) -> None:
        self.final = final

    async def astream(self, state, stream_mode):
        assert stream_mode == ["updates", "values"]
        for step in TRACE:
            yield "updates", {step["node"]: {"trace": [step]}}
        yield "updates", {"odd_node": "not a dict"}
        yield "values", self.final

    async def ainvoke(self, state):
        return self.final


def plan(*extra: str, graph, stderr=None):
    args = build_parser().parse_args(
        ["route", "plan", "--origin", "a", "--destination", "b", "--no-record", *extra]
    )
    out, err = io.StringIO(), stderr or io.StringIO()
    rc = route_cmd.run(args, out, err, graph_factory=lambda: graph)
    return rc, out.getvalue(), err.getvalue()


def test_format_ms_reads_like_a_person_would_say_it():
    assert [format_ms(v) for v in (0, 820.4, 4200, 125000, None, -1)] == [
        "0 ms",
        "820 ms",
        "4.2 s",
        "2 min 5 s",
        "-",
        "-",
    ]


def test_trace_lines_list_the_steps_mark_the_slowest_and_total():
    lines = trace_lines(TRACE)
    assert lines[0] == "Where the time went:"
    assert any("Routing" in line and "8.2 s" in line and "<- slowest" in line for line in lines)
    assert lines[-1].strip().startswith("Total") and "8.2 s" in lines[-1]
    assert sum("<- slowest" in line for line in lines) == 1
    assert trace_lines([]) == [] and trace_lines([{"ms": 1}]) == []


def test_text_output_ends_with_the_trace_only_when_the_plan_has_one():
    payload = {"status": "ready", "route": {}, "errors": []}
    assert "Where the time went" not in plan_text(payload)
    assert "Where the time went" in plan_text({**payload, "trace": TRACE})


def test_trace_is_a_key_of_the_json_and_absent_without_the_flag():
    state = {**READY_STATE, "trace": TRACE}
    rc, out, _ = plan("--trace", graph=FakeGraph(state))
    assert rc == 0 and json.loads(out)["trace"] == TRACE
    rc, out, _ = plan(graph=FakeGraph(state))
    assert "trace" not in json.loads(out)


def test_trace_in_text_and_a_plan_that_has_none():
    rc, out, _ = plan(
        "--trace", "--format", "text", graph=FakeGraph({**READY_STATE, "trace": TRACE})
    )
    assert "Where the time went:" in out and "Routing" in out
    rc, out, _ = plan("--trace", graph=FakeGraph(READY_STATE))
    assert json.loads(out)["trace"] == []  # asked for, nothing recorded: an empty list, not a crash


def test_progress_lines_go_to_stderr_when_it_is_a_terminal():
    rc, out, err = plan(graph=StreamingGraph(READY_STATE), stderr=Terminal())
    assert rc == 0 and json.loads(out)["status"] == "ready"  # stdout is still only the result
    lines = err.splitlines()
    assert lines[0] == "  Reading the request ... 0 ms"
    assert lines[1] == "  Routing ... 8.2 s"
    assert lines[2] == "  Writing the files ... 40 ms"
    assert lines[3] == "  odd_node ..."  # an unknown step with no timing is still shown, not fatal


def test_nothing_is_printed_when_stderr_is_not_a_terminal_or_with_quiet():
    graph = StreamingGraph(READY_STATE)
    assert (
        plan(graph=graph)[0] == 0 and plan(graph=graph)[2] == ""
    )  # a pipe or a file: no progress noise
    assert plan("-q", graph=graph, stderr=Terminal())[2] == ""
    assert plan("--quiet", graph=graph, stderr=Terminal())[2] == ""


def test_a_graph_that_cannot_stream_is_invoked_even_on_a_terminal():
    rc, out, err = plan(graph=FakeGraph(READY_STATE), stderr=Terminal())
    assert rc == 0 and err == "" and json.loads(out)["status"] == "ready"


@pytest.mark.parametrize("flag", ["--trace", "--quiet", "-q"])
def test_the_flags_are_in_the_help(flag, capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["route", "plan", "--help"])
    assert flag in capsys.readouterr().out


def test_a_saved_trace_with_a_bad_duration_does_not_break_route_show():
    bad = [
        {"node": "parse_request", "ms": "unknown"},
        {"node": "route_with_provider", "ms": 2500.0},
        {"node": "explain_and_export", "ms": None},
        {"node": "x", "ms": True},
        {"node": "y", "ms": float("nan")},
    ]
    lines = trace_lines(bad)
    assert any("Routing" in line and "2.5 s" in line and "<- slowest" in line for line in lines)
    assert lines[-1].strip().startswith("Total") and "2.5 s" in lines[-1]
    assert format_ms("slow") == "-" and format_ms(True) == "-" and format_ms(float("nan")) == "-"
    assert "Where the time went" in plan_text({"status": "ready", "route": {}, "trace": bad})
