"""Listing and deleting conversations: summaries for the ids a client names, and delete."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from bike_routing_agent.chat.service import TITLE_CHARS, ChatService
from bike_routing_agent.chat.store import SqliteChatSaver

from .test_chat_graph import FakePlanner, Talk


async def talk(service: ChatService, *messages: str) -> str:
    t = Talk(service)
    for message in messages:
        await t.say(message)
    assert t.sid is not None
    return t.sid


def rows(db: Path) -> set[str]:
    with sqlite3.connect(db) as conn:
        return {r[0] for r in conn.execute("SELECT thread_id FROM chat_threads")}


# ------------------------------------------------------------------------------ summaries


async def test_a_summary_has_the_first_question_as_title_and_counts_the_messages():
    service = ChatService(FakePlanner())
    sid = await talk(service, "from Braunschweig to Goslar by gravel", "make it shorter")
    (summary,) = await service.summaries([sid])
    assert summary.session_id == sid
    assert summary.title == "from Braunschweig to Goslar by gravel"  # not the last thing said
    assert summary.messages == 4  # two questions, two answers
    assert abs(summary.updated - time.time()) < 5


async def test_a_long_or_untidy_first_question_is_shortened_and_cleaned_for_a_list():
    service = ChatService(FakePlanner())
    long = await talk(service, "from Braunschweig to Goslar " + "x" * 80)
    untidy = await talk(service, "plan   a\n route")
    summaries = {s.session_id: s for s in await service.summaries([long, untidy])}
    assert len(summaries[long].title) == TITLE_CHARS and summaries[long].title.endswith("…")
    assert summaries[untidy].title == "plan a route"


async def test_only_the_named_and_known_conversations_are_returned_most_recent_first():
    service = ChatService(FakePlanner())
    first = await talk(service, "A to B")
    time.sleep(0.01)
    second = await talk(service, "C to D")
    time.sleep(0.01)
    await service.send("make it shorter", session_id=first)  # first is now the newest
    unknown = "a" * 32
    found = await service.summaries([second, unknown, first, first])  # a duplicate, an unknown
    assert [s.session_id for s in found] == [first, second]
    assert await service.summaries([]) == [] and await service.summaries([unknown]) == []


async def test_a_conversation_nobody_named_is_not_listed():
    service = ChatService(FakePlanner())
    mine = await talk(service, "A to B")
    await talk(service, "C to D")  # someone else's
    assert [s.session_id for s in await service.summaries([mine])] == [mine]


async def test_the_last_use_survives_a_restart(tmp_path):
    db = tmp_path / "chat.sqlite"
    service = ChatService(FakePlanner(), checkpointer=SqliteChatSaver(db))
    sid = await talk(service, "A to B")
    before = (await service.summaries([sid]))[0].updated
    service._checkpointer.close()
    time.sleep(0.02)

    again = ChatService(FakePlanner(), checkpointer=SqliteChatSaver(db))
    (summary,) = await again.summaries([sid])
    assert summary.updated == pytest.approx(before, abs=1.0)  # from the file, not "now"
    assert summary.title == "A to B"


# -------------------------------------------------------------------------------- delete


async def test_a_deleted_conversation_is_gone_and_the_others_stay():
    service = ChatService(FakePlanner())
    gone = await talk(service, "A to B")
    kept = await talk(service, "C to D")
    assert await service.delete(gone) is True
    assert await service.history(gone) is None and await service.summaries([gone]) == []
    assert service.sessions == 1 and await service.history(kept) is not None


async def test_deleting_an_unknown_conversation_says_so():
    service = ChatService(FakePlanner())
    assert await service.delete("b" * 32) is False
    sid = await talk(service, "A to B")
    assert await service.delete(sid) is True and await service.delete(sid) is False


async def test_a_deleted_conversation_is_removed_from_the_file_too(tmp_path):
    db = tmp_path / "chat.sqlite"
    service = ChatService(FakePlanner(), checkpointer=SqliteChatSaver(db))
    gone = await talk(service, "A to B")
    kept = await talk(service, "C to D")
    assert rows(db) == {gone, kept}
    await service.delete(gone)
    assert rows(db) == {kept}
    service._checkpointer.close()
    assert ChatService(FakePlanner(), checkpointer=SqliteChatSaver(db)).sessions == 1


async def test_a_deleted_session_id_cannot_be_continued():
    service = ChatService(FakePlanner())
    sid = await talk(service, "A to B")
    await service.delete(sid)
    reply = await service.send("make it shorter", session_id=sid)
    assert reply.session_id != sid  # a new conversation, not the old one back
