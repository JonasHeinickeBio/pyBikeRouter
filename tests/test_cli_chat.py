"""``bike-router chat``: scripted turns and the interactive loop, with a scripted planner."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from bike_routing_agent.chat.service import ChatService
from bike_routing_agent.cli import main
from bike_routing_agent.cli.commands import chat as chat_cmd
from bike_routing_agent.cli.main import build_parser


def plan(files: bool = True) -> dict[str, Any]:
    first = {
        "provider": "brouter",
        "provider_profile": "custom_gravel-v2",
        "rank": 1,
        "metrics": {"distance_m": 30_000.0, "duration_s": 4200.0, "ascent_m": 100.0},
        "geometry_geojson": {"type": "LineString", "coordinates": [[10.5, 52.2], [10.6, 52.3]]},
        "pros": [],
        "cons": [],
    }
    return {
        "status": "ready",
        "route": first,
        "candidates": [first],
        "artifacts": {"gpx_url": "/v1/routes/" + "a" * 32 + ".gpx"} if files else {},
        "errors": [],
    }


class Planner:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def plan(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        return plan()

    async def plan_text(self, text: str, timezone: str | None) -> dict[str, Any]:
        return plan()


def factory() -> tuple[ChatService, Planner]:
    planner = Planner()
    return ChatService(planner), planner


def run(argv: list[str], *, service: ChatService | None = None, lines: list[str] | None = None):
    service = service or factory()[0]
    out, err = io.StringIO(), io.StringIO()
    feed = iter(lines or [])

    def reader(prompt: str) -> str:
        try:
            return next(feed)
        except StopIteration:
            raise EOFError from None

    args = build_parser().parse_args(["chat", "start", *argv])
    code = chat_cmd.run(args, out, err, service_factory=lambda: service, reader=reader)
    return code, out.getvalue(), err.getvalue()


def test_scripted_messages_are_one_conversation_and_print_each_turn():
    service, planner = factory()
    code, out, _ = run(
        ["-m", "from Braunschweig to Goslar", "-m", "make it shorter"], service=service
    )
    assert code == 0
    assert "you> from Braunschweig to Goslar" in out and "bot> Here is your route: 30.0 km" in out
    assert "you> make it shorter" in out and "Okay: at most 22.5 km" in out
    assert [r["origin"] for r in planner.requests] == [
        "Braunschweig",
        "Braunschweig",
    ]  # one session
    assert "[1] Alternatives" not in out  # a single candidate: nothing to compare
    assert "[1] Make it shorter" in out


def test_quick_replies_can_be_chosen_by_number_in_the_interactive_loop():
    service, planner = factory()
    code, out, _ = run([], service=service, lines=["Braunschweig to Goslar", "1", "quit"])
    assert code == 0 and "Plan a bike route by chatting" in out
    # "1" picked the first quick reply of the previous answer ("Make it shorter").
    assert len(planner.requests) == 2 and "max_distance_km" in planner.requests[1]["constraints"]


def test_numbers_are_sent_as_they_are_when_the_question_wants_numbers():
    ambiguous = {
        "status": "awaiting_clarification",
        "clarification": [
            {
                "field": "Neustadt",
                "candidates": [
                    {"label": "Neustadt A", "coordinate": {"lon": 1.0, "lat": 2.0}},
                    {"label": "Neustadt B", "coordinate": {"lon": 3.0, "lat": 4.0}},
                ],
            }
        ],
        "errors": [],
    }

    class Ambiguous(Planner):
        async def plan(self, request: dict[str, Any]) -> dict[str, Any]:
            self.requests.append(request)
            return ambiguous if request["origin"] == "Neustadt" else plan()

    planner = Ambiguous()
    code, out, _ = run([], service=ChatService(planner), lines=["Neustadt to Goslar", "2", "exit"])
    assert code == 0 and "1 | 2" in out and "Neustadt B" in out
    assert planner.requests[1]["origin"] == {"lon": 3.0, "lat": 4.0}


def test_route_files_are_shown_as_files_in_the_export_folder(monkeypatch: pytest.MonkeyPatch):
    from bike_routing_agent.config import settings

    monkeypatch.setattr(settings, "export_dir", "/data/exports/")
    _, out, _ = run(["-m", "A to B", "-m", "send me the gpx"])
    assert f"/data/exports/{'a' * 32}.gpx" in out and "/v1/routes/" not in out


def test_end_of_input_blank_lines_and_quit_words_leave_cleanly():
    assert run([], lines=["", "   "])[0] == 0  # blank lines skipped, then EOF
    assert run([], lines=["quit", "A to B"])[0] == 0


def test_json_format_prints_one_object_per_turn_without_the_whole_route():
    code, out, _ = run(["-m", "A to B", "-m", "alternatives", "--format", "json"])
    lines = [json.loads(line) for line in out.strip().splitlines()]
    assert code == 0 and len(lines) == 2 and lines[0]["intent"] == "plan"
    assert lines[0]["plan"] == {
        "status": "ready",
        "candidates": 1,
        "artifacts": plan()["artifacts"],
    }
    assert "geometry_geojson" not in out


def test_a_switched_off_chat_is_reported(monkeypatch: pytest.MonkeyPatch):
    import bike_routing_agent.api as api_module

    monkeypatch.setattr(api_module, "_chat_service", None)
    out, err = io.StringIO(), io.StringIO()
    code = main(["chat", "start", "-m", "hi"], stdout=out, stderr=err)
    assert code == 1 and "switched off" in err.getvalue()


def test_chat_is_registered_and_needs_a_command():
    assert "chat" in build_parser().format_help()
    out, err = io.StringIO(), io.StringIO()
    assert main(["chat"], stdout=out, stderr=err) == 2
