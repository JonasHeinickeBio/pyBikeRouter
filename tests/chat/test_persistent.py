"""Chat sessions that outlive the process: the SQLite checkpointer and what the service does
with it (finding sessions again after a restart, the stored history, bounds and bad rows)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from bike_routing_agent.chat.service import ChatService
from bike_routing_agent.chat.store import SqliteChatSaver, _decode, _encode

from .test_chat_graph import FakePlanner, Talk, ambiguous_once


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "chat" / "sessions.sqlite"


def start(
    db: Path, planner: FakePlanner | None = None, **kw: Any
) -> tuple[ChatService, FakePlanner]:
    """A service on the file, as a freshly started process would build it."""
    planner = planner or FakePlanner()
    saver = SqliteChatSaver(db, max_age_days=kw.pop("max_age_days", 30.0))
    return ChatService(planner, checkpointer=saver, **kw), planner


def rows(db: Path) -> dict[str, tuple[str, float]]:
    with sqlite3.connect(db) as conn:
        found = conn.execute("SELECT thread_id, layout, updated FROM chat_threads").fetchall()
    return {thread: (layout, updated) for thread, layout, updated in found}


# ----------------------------------------------------------------------------- the encoding


def test_the_row_encoding_round_trips_bytes_tuples_and_dicts():
    value = {
        "": {"cp1": (("msgpack", b"\x00\xffdata"), ("json", b"{}"), None)},
        "k": [("a", 1, 2.5, None, True), {"nested": (b"x",)}],
    }
    text = json.dumps(_encode(value))  # what lands in the file: plain JSON
    assert _decode(json.loads(text)) == value


def test_an_unknown_tag_in_a_row_is_refused_instead_of_guessed():
    with pytest.raises(ValueError):
        _decode({"$pickle": "gASV"})


# ------------------------------------------------------------------------ surviving a restart


async def test_a_conversation_continues_after_a_restart(db):
    service, _ = start(db)
    first = await Talk(service).say("from Braunschweig to Goslar by gravel")
    assert first.plan is not None
    service._checkpointer.close()

    again, planner = start(db)  # a new process, the same file
    assert again.sessions == 1
    reply = await again.send("make it shorter", session_id=first.session_id)
    assert reply.session_id == first.session_id  # not a new conversation
    assert reply.intent == "refine"
    assert planner.requests[0]["origin"] == "Braunschweig"  # it remembered the request


async def test_a_session_that_was_never_stored_starts_a_new_conversation(db):
    service, _ = start(db)
    reply = await service.send("hello", session_id="a" * 32)
    assert reply.session_id != "a" * 32


async def test_an_open_guided_question_is_still_open_after_a_restart(db):
    service, _ = start(db)
    t = Talk(service)
    asked = await t.say("plan a route")
    assert asked.awaiting == "guided"
    service._checkpointer.close()

    again, planner = start(db)
    reply = await again.send("Goslar", session_id=t.sid)
    assert reply.session_id == t.sid and reply.awaiting == "guided"  # the next question
    assert planner.requests == []


async def test_a_paused_place_choice_resumes_after_a_restart(db):
    service, _ = start(db, FakePlanner(ambiguous_once))
    t = Talk(service)
    asked = await t.say("Springfield to Boston")
    assert asked.awaiting == "place_choice"
    service._checkpointer.close()

    again, planner = start(db, FakePlanner(ambiguous_once))
    history = await again.history(t.sid)
    assert history is not None and history.awaiting == "place_choice"
    assert history.messages[-1].role == "bot" and "Springfield" in history.messages[-1].text
    assert history.suggestions == ["1", "2"]

    reply = await again.send("2", session_id=t.sid)
    assert planner.requests[0]["origin"] == {"lon": -72.6, "lat": 42.1}
    assert reply.plan is not None and reply.awaiting is None


async def test_only_the_latest_checkpoint_of_a_conversation_is_stored(db):
    service, _ = start(db)
    t = Talk(service)
    for text in ["A to B", "make it shorter", "make it longer", "make it shorter"]:
        await t.say(text)
    saver = service._checkpointer
    assert all(len(cps) == 1 for cps in saver.storage[t.sid].values())
    assert len(rows(db)) == 1


# --------------------------------------------------------------------------------- history


async def test_the_history_has_both_sides_the_suggestions_and_the_last_route(db):
    service, _ = start(db)
    t = Talk(service)
    await t.say("from Braunschweig to Goslar by gravel")
    last = await t.say("make it shorter")
    history = await service.history(t.sid)
    assert history is not None and history.session_id == t.sid
    assert [m.role for m in history.messages] == ["user", "bot", "user", "bot"]
    assert history.messages[0].text == "from Braunschweig to Goslar by gravel"
    assert history.messages[-1].text == last.reply
    assert history.suggestions == last.suggestions
    assert history.plan is not None and history.plan["status"] == "ready"
    assert history.awaiting is None


async def test_the_history_of_an_unknown_session_is_none(db):
    service, _ = start(db)
    assert await service.history("b" * 32) is None


async def test_the_history_works_for_a_memory_only_service_too():
    service = ChatService(FakePlanner())
    t = Talk(service)
    await t.say("A to B")
    assert (await service.history(t.sid)).plan is not None  # type: ignore[union-attr]


# ------------------------------------------------------------------------------- bounds


async def test_the_oldest_session_is_dropped_from_the_file_when_there_are_too_many(db):
    service, _ = start(db, max_sessions=1)
    first = await Talk(service).say("A to B")
    second = await Talk(service).say("C to D")
    assert set(rows(db)) == {second.session_id}
    assert await service.history(first.session_id) is None


async def test_sessions_beyond_the_limit_are_dropped_at_start(db):
    service, _ = start(db)
    for text in ["A to B", "C to D", "E to F"]:
        await Talk(service).say(text)
    service._checkpointer.close()
    again, _ = start(db, max_sessions=2)
    assert again.sessions == 2 and len(rows(db)) == 2


def test_conversations_older_than_the_limit_are_not_loaded_and_are_deleted(db):
    saver = SqliteChatSaver(db)
    saver.persist("old")
    saver.persist("new")
    saver.close()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE chat_threads SET updated = ? WHERE thread_id = 'old'",
            (time.time() - 40 * 86400,),
        )
    again = SqliteChatSaver(db, max_age_days=30)
    assert again.thread_ids() == ["new"] and set(rows(db)) == {"new"}


async def test_the_sessions_come_back_least_recently_used_first(db):
    service, _ = start(db)
    a = await Talk(service).say("A to B")
    b = await Talk(service).say("C to D")
    await service.send("make it shorter", session_id=a.session_id)  # a is now the newest
    service._checkpointer.close()
    assert SqliteChatSaver(db).thread_ids() == [b.session_id, a.session_id]


# ------------------------------------------------------------------------------ bad files


async def test_an_unreadable_row_is_dropped_and_the_others_are_kept(db):
    service, _ = start(db)
    good = await Talk(service).say("A to B")
    bad = await Talk(service).say("C to D")
    service._checkpointer.close()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE chat_threads SET data = 'not json' WHERE thread_id = ?", (bad.session_id,)
        )

    again, _ = start(db)
    assert again.sessions == 1 and set(rows(db)) == {good.session_id}
    assert (await again.history(good.session_id)) is not None
    assert await again.history(bad.session_id) is None


async def test_a_row_written_with_another_checkpoint_layout_is_dropped(db):
    service, _ = start(db)
    reply = await Talk(service).say("A to B")
    service._checkpointer.close()
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE chat_threads SET layout = '0:0.1'")
    again, _ = start(db)
    assert again.sessions == 0 and rows(db) == {}
    assert await again.history(reply.session_id) is None


async def test_a_row_that_fails_half_way_leaves_nothing_of_the_conversation_behind(db):
    saver = SqliteChatSaver(db)
    saver.persist("half")
    saver.close()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE chat_threads SET data = ? WHERE thread_id = 'half'",
            (
                json.dumps(
                    {"storage": _encode({"": {"cp": ("x",)}}), "writes": [], "blobs": "oops"}
                ),
            ),
        )
    again = SqliteChatSaver(db)
    assert again.thread_ids() == [] and "half" not in again.storage


async def test_a_failing_store_does_not_fail_the_turn(db, monkeypatch):
    service, _ = start(db)

    def boom(thread_id: str) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(service._checkpointer, "persist", boom)
    reply = await Talk(service).say("A to B")
    assert reply.plan is not None and not reply.failed


def test_the_file_and_its_folder_are_created(tmp_path):
    path = tmp_path / "a" / "b" / "chat.sqlite"
    saver = SqliteChatSaver(path)
    assert path.is_file() and saver.path == path


def test_a_deleted_thread_is_gone_from_memory_and_file(db):
    saver = SqliteChatSaver(db)
    saver.persist("t")
    assert set(rows(db)) == {"t"}
    saver.delete_thread("t")
    assert rows(db) == {}
