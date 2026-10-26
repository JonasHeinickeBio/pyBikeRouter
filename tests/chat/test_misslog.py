"""The opt-in log of what the chat could not read: the file, and what counts as a miss."""

from __future__ import annotations

import json
import logging

import pytest

from bike_routing_agent.chat import misslog
from bike_routing_agent.chat.misslog import MissLog
from bike_routing_agent.chat.models import PlanError

from .test_chat_graph import FakePlanner, Talk, chat

# ------------------------------------------------------------------------------ the file


def test_a_miss_is_one_line_of_json_with_no_session_or_place_data(tmp_path):
    log = MissLog(tmp_path / "logs" / "misses.jsonl")  # the folder is made
    assert log.record(
        "  show me something nice  ", "unread", intent="guided", had_plan=False, now=5.0
    )
    (line,) = (tmp_path / "logs" / "misses.jsonl").read_text().splitlines()
    assert json.loads(line) == {
        "at": 5.0,
        "kind": "unread",
        "intent": "guided",
        "had_plan": False,
        "text": "show me something nice",
    }


def test_a_long_message_is_cut(tmp_path):
    log = MissLog(tmp_path / "m.jsonl")
    log.record("x" * 900, "unread", intent=None, had_plan=True)
    assert len(misslog.load(tmp_path / "m.jsonl")[0]["text"]) == misslog.MAX_TEXT_CHARS


def test_the_file_is_capped_by_keeping_the_old_part_once(tmp_path):
    path = tmp_path / "m.jsonl"
    log = MissLog(path, max_bytes=300)
    for i in range(12):
        log.record(f"message number {i} " + "y" * 30, "unread", intent=None, had_plan=False)
    assert path.stat().st_size < 600 and path.with_name("m.jsonl.1").exists()
    texts = [r["text"].split()[2] for r in misslog.load(path)]
    assert texts == sorted(texts, key=int)  # the rotated copy comes first, then the new file
    assert texts[-1] == "11"
    assert log.clear() == 2 and not path.exists() and log.clear() == 0


def test_a_log_that_cannot_be_written_says_so_once_and_never_raises(tmp_path, caplog):
    blocked = tmp_path / "is-a-directory"
    blocked.mkdir()
    log = MissLog(blocked)
    caplog.set_level(logging.WARNING, logger="bike_routing_agent.chat.misslog")
    assert log.record("a", "unread", intent=None, had_plan=False) is False
    assert log.record("b", "unread", intent=None, had_plan=False) is False
    assert sum("cannot be written" in r.message for r in caplog.records) == 1


def test_loading_skips_lines_that_are_not_misses(tmp_path):
    path = tmp_path / "m.jsonl"
    path.write_text(
        'not json\n[1, 2]\n{"kind": "unread"}\n{"text": "no kind"}\n'
        '{"kind": "unread", "text": "good", "had_plan": false}\n\n',
        encoding="utf-8",
    )
    assert [r["text"] for r in misslog.load(path)] == ["good"]
    assert misslog.load(tmp_path / "missing.jsonl") == []


def test_a_phrase_written_slightly_differently_counts_once():
    records = [
        {"kind": "unread", "text": "Something NICE for Sunday?"},
        {"kind": "unread", "text": "something  nice for sunday"},
        {"kind": "refine_unknown", "text": "make it purple!"},
    ]
    summary = misslog.summarise(records)
    assert summary["total"] == 3 and summary["by_kind"] == {"unread": 2, "refine_unknown": 1}
    assert summary["top"][0] == {"text": "something nice for sunday", "count": 2}
    assert misslog.summarise([])["top"] == []


def test_exported_cases_are_distinct_and_unlabelled():
    records = [
        {"kind": "unread", "text": "Something nice?", "intent": "guided", "had_plan": False},
        {"kind": "unread", "text": "something nice", "intent": "guided", "had_plan": False},
        {"kind": "refine_unknown", "text": "make it purple", "intent": "refine", "had_plan": True},
        {"kind": "unread", "text": "   ", "had_plan": False},
    ]
    cases = misslog.candidate_cases(records)
    assert [c["text"] for c in cases] == ["Something nice?", "make it purple"]
    assert [c["state"] for c in cases] == ["none", "route"]
    assert all(c["expected"] is None for c in cases)  # a person decides what it should have been
    assert cases[0]["id"] == "miss-001" and cases[1]["seen_as"] == "refine"


# ------------------------------------------------------------------------------ what is a miss


def logged(tmp_path):
    return misslog.load(tmp_path / "m.jsonl")


def service(tmp_path, planner=None, **kw):
    return chat(planner, miss_log=MissLog(tmp_path / "m.jsonl"), **kw)


async def test_free_text_the_dialogue_could_not_use_is_a_miss(tmp_path):
    svc, _ = service(tmp_path)
    reply = await Talk(svc).say("show me something nice for the weekend")
    assert "where do you want to start" in reply.reply.lower()
    (miss,) = logged(tmp_path)
    assert miss["kind"] == "unread" and miss["had_plan"] is False
    assert miss["text"] == "show me something nice for the weekend" and miss["intent"] == "guided"


@pytest.mark.parametrize(
    "text",
    [
        "hi",
        "hello!",
        "plan a route",
        "I want to plan a ride",
        "route please",
        "Goslar",
        "40 km loop from Goslar",
        "from Braunschweig to Goslar",
        "help",
        "new route",
    ],
)
async def test_messages_the_chat_does_read_are_not_misses(tmp_path, text):
    svc, _ = service(tmp_path)
    await Talk(svc).say(text)
    assert logged(tmp_path) == []


async def test_an_answer_to_a_question_is_not_a_miss(tmp_path):
    svc, _ = service(tmp_path)
    t = Talk(svc)
    for text in ["plan a route", "Goslar", "a loop", "30", "gravel"]:
        await t.say(text)
    assert logged(tmp_path) == []


async def test_a_change_the_chat_could_not_make_out_is_a_miss_with_the_route_on_screen(tmp_path):
    svc, _ = service(tmp_path)
    t = Talk(svc)
    await t.say("40 km loop from Goslar")
    reply = await t.say("make it more purple")
    assert "did not catch" in reply.reply.lower() or "try 'shorter'" in reply.reply.lower()
    (miss,) = logged(tmp_path)
    assert miss["kind"] == "refine_unknown" and miss["had_plan"] is True


async def test_free_text_the_model_could_not_read_is_a_miss(tmp_path):
    planner = FakePlanner()
    planner.text_response = PlanError("the language model could not read that")
    svc, _ = service(tmp_path, planner, text_enabled=True)
    reply = await Talk(svc).say("a gloomy cruise through the fog")
    assert reply.failed
    (miss,) = logged(tmp_path)
    assert miss["kind"] == "text_failed" and miss["intent"] == "plan_text"


async def test_without_a_log_path_nothing_is_written_anywhere(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    svc, _ = chat()
    await Talk(svc).say("show me something nice for the weekend")
    assert list(tmp_path.iterdir()) == []


async def test_a_log_that_fails_never_fails_the_turn(tmp_path):
    (tmp_path / "m.jsonl").mkdir()
    svc, _ = service(tmp_path)
    reply = await Talk(svc).say("show me something nice for the weekend")
    assert reply.failed is False and reply.reply
