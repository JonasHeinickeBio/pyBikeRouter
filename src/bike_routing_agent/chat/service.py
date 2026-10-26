"""``ChatService``: sessions on top of the chat graph.

A session is a LangGraph *thread*: the checkpointer keeps the conversation state between
requests, and a pending question (which of two Springfields?) is a paused graph that the next
message resumes. By default sessions live in memory -- a restart forgets them; with a
``SqliteChatSaver`` (``CHAT_STORE_PATH``) they are kept in a file and found again after one. Their
number is bounded either way: the least recently used one is dropped first.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import Sequence
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from bike_routing_agent.chat.graph import build_chat_graph
from bike_routing_agent.chat.models import (
    MAX_MESSAGE_CHARS,
    ChatHistory,
    ChatMessage,
    ChatReply,
    ChatSummary,
    Planner,
    Sights,
)
from bike_routing_agent.chat.store import LatestOnlyMemorySaver

logger = logging.getLogger(__name__)

SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
TITLE_CHARS = 60


def _title(text: str) -> str:
    """The first thing that was asked, shortened for a list."""
    return text if len(text) <= TITLE_CHARS else text[: TITLE_CHARS - 1].rstrip() + "\u2026"


class TurnSuperseded(Exception):
    """A newer message in the same conversation replaced this turn while it was still running."""


FAILURE_REPLY = (
    "Something went wrong while planning that. Please try again, or rephrase "
    "('from A to B', '40 km loop from A')."
)


class ChatService:
    def __init__(
        self,
        planner: Planner,
        *,
        sights: Sights | None = None,
        text_enabled: bool = False,
        checkpointer: BaseCheckpointSaver | None = None,
        max_sessions: int = 200,
    ) -> None:
        self._checkpointer = checkpointer or LatestOnlyMemorySaver()
        self._graph = build_chat_graph(
            planner=planner,
            sights=sights,
            text_enabled=text_enabled,
            checkpointer=self._checkpointer,
        )
        self._max_sessions = max_sessions
        self._sessions: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._updated: dict[str, float] = {}
        self._running: dict[str, asyncio.Task[ChatReply]] = {}
        self._superseded: set[asyncio.Task[ChatReply]] = set()
        stored = getattr(self._checkpointer, "thread_ids", None)
        when = getattr(self._checkpointer, "updated_at", None)
        for sid in stored() if callable(stored) else ():  # least recently used first
            self._touch(sid)
            self._updated[sid] = (when(sid) if callable(when) else None) or time.time()

    @property
    def sessions(self) -> int:
        return len(self._sessions)

    async def send(
        self,
        message: str,
        *,
        session_id: str | None = None,
        timezone: str | None = None,
        form_context: dict[str, Any] | None = None,
    ) -> ChatReply:
        """One turn. A message that arrives while the conversation's previous turn is still
        running *replaces* it: the old turn is cancelled (its planning stops, its routing requests
        that are still queued are never sent) and its caller gets ``TurnSuperseded``. Without that,
        a person who corrects themselves would wait behind a plan they no longer want."""
        text = message.strip()[:MAX_MESSAGE_CHARS]
        sid = session_id if session_id and session_id in self._sessions else uuid.uuid4().hex
        previous = self._running.get(sid)
        if previous is not None and not previous.done():
            self._superseded.add(previous)
            previous.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await previous  # its lock is free once it has unwound
        task = asyncio.ensure_future(self._turn(sid, text, timezone, form_context))
        self._running[sid] = task
        try:
            return await task
        except asyncio.CancelledError:
            if task in self._superseded:
                raise TurnSuperseded from None
            task.cancel()  # this caller went away (a closed connection): stop the turn with it
            raise
        finally:
            self._superseded.discard(task)
            if self._running.get(sid) is task:
                del self._running[sid]

    async def _turn(
        self, sid: str, text: str, timezone: str | None, form_context: dict[str, Any] | None
    ) -> ChatReply:
        lock = self._touch(sid)
        config = {"configurable": {"thread_id": sid}}
        async with lock:
            try:
                snapshot = await self._graph.aget_state(config)
                if any(task.interrupts for task in snapshot.tasks):
                    # a question is open: this message is its answer. (A turn that was replaced
                    # half-way also leaves "next" steps behind, but no question: it starts afresh.)
                    result = await self._graph.ainvoke(Command(resume=text), config)
                else:
                    result = await self._graph.ainvoke(
                        {"user_text": text, "timezone": timezone, "form_context": form_context},
                        config,
                    )
            except Exception:  # a turn must never crash the conversation
                logger.exception("chat turn failed")
                return ChatReply(
                    session_id=sid, reply=FAILURE_REPLY, suggestions=["New route"], failed=True
                )
            finally:
                self._keep_latest_only(sid)
                self._save(sid)
                self._updated[sid] = time.time()
        return self._reply(sid, result)

    async def summaries(self, session_ids: Sequence[str]) -> list[ChatSummary]:
        """The conversations among ``session_ids`` that are known, most recently used first.

        Only the ids the caller names are looked up, so the service never lists conversations
        nobody asked about: a session id stays the one handle to a conversation.
        """
        found: list[ChatSummary] = []
        for sid in dict.fromkeys(session_ids):
            lock = self._sessions.get(sid)
            if lock is None:
                continue
            async with lock:
                snapshot = await self._graph.aget_state({"configurable": {"thread_id": sid}})
            humans = [
                " ".join(str(m.content).split())
                for m in (snapshot.values or {}).get("messages") or []
                if m.type == "human" and str(m.content).strip()
            ]
            messages = sum(
                1
                for m in (snapshot.values or {}).get("messages") or []
                if m.type in ("human", "ai") and str(m.content).strip()
            )
            found.append(
                ChatSummary(
                    session_id=sid,
                    title=_title(humans[0]) if humans else "New conversation",
                    updated=self._updated.get(sid, 0.0),
                    messages=messages,
                )
            )
        return sorted(found, key=lambda c: -c.updated)

    async def delete(self, session_id: str) -> bool:
        """Forget a conversation (memory and file); ``False`` when it is not known."""
        lock = self._sessions.get(session_id)
        if lock is None:
            return False
        async with lock:  # let a turn that is running finish first
            self._sessions.pop(session_id, None)
            self._updated.pop(session_id, None)
            delete = getattr(self._checkpointer, "delete_thread", None)
            if delete is not None:
                delete(session_id)
        return True

    async def history(self, session_id: str) -> ChatHistory | None:
        """What a stored conversation said so far, or ``None`` when it is not known."""
        if session_id not in self._sessions:
            return None
        config = {"configurable": {"thread_id": session_id}}
        async with self._sessions[session_id]:
            snapshot = await self._graph.aget_state(config)
        values = snapshot.values or {}
        messages = [
            ChatMessage(role="user" if m.type == "human" else "bot", text=str(m.content))
            for m in values.get("messages") or []
            if m.type in ("human", "ai") and str(m.content).strip()
        ]
        suggestions = list(values.get("suggestions") or [])
        awaiting: str | None = "guided" if values.get("asking") else None
        for task in snapshot.tasks:  # a question is open: it was never added to the messages
            for interrupt in task.interrupts:
                payload = interrupt.value or {}
                if payload.get("reply"):
                    messages.append(ChatMessage(role="bot", text=str(payload["reply"])))
                suggestions = list(payload.get("suggestions", []))
                awaiting = payload.get("awaiting")
        return ChatHistory(
            session_id=session_id,
            messages=messages,
            suggestions=suggestions,
            plan=values.get("last_plan"),
            focus_rank=values.get("focus_rank"),
            request=values.get("last_request") if values.get("last_plan") else None,
            awaiting=awaiting,  # type: ignore[arg-type]
        )

    def _save(self, sid: str) -> None:
        """Write the conversation down, when the checkpointer keeps them (never fails a turn)."""
        persist = getattr(self._checkpointer, "persist", None)
        if persist is None:
            return
        try:
            persist(sid)
        except Exception:
            logger.exception("could not store chat session %s", sid)

    def _keep_latest_only(self, sid: str) -> None:
        """Bound memory: a thread needs only its latest checkpoint (see LatestOnlyMemorySaver)."""
        try:
            self._checkpointer.prune([sid], strategy="keep_latest")
        except NotImplementedError:
            pass  # a checkpointer that cannot prune keeps its history

    def _touch(self, sid: str) -> asyncio.Lock:
        lock = self._sessions.get(sid)
        if lock is None:
            lock = self._sessions[sid] = asyncio.Lock()
        self._sessions.move_to_end(sid)
        while len(self._sessions) > self._max_sessions:
            old, _ = self._sessions.popitem(last=False)
            self._updated.pop(old, None)
            delete = getattr(self._checkpointer, "delete_thread", None)
            if delete is not None:
                delete(old)
        return lock

    @staticmethod
    def _reply(sid: str, result: dict[str, Any]) -> ChatReply:
        interrupts = result.get("__interrupt__") or []
        if interrupts:
            payload = interrupts[0].value or {}
            return ChatReply(
                session_id=sid,
                reply=str(payload.get("reply", "")),
                suggestions=list(payload.get("suggestions", [])),
                intent=result.get("intent"),
                awaiting=payload.get("awaiting"),
            )
        return ChatReply(
            session_id=sid,
            reply=result.get("reply") or "",
            suggestions=list(result.get("suggestions") or []),
            plan=result.get("plan"),
            focus_rank=result.get("focus_rank"),
            request=result.get("last_request") if result.get("plan") else None,
            intent=result.get("intent"),
            awaiting="guided" if result.get("asking") else None,
            failed=bool(result.get("failed")),
        )
