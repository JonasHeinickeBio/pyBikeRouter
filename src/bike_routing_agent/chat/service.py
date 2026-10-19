"""``ChatService``: sessions on top of the chat graph.

A session is a LangGraph *thread*: the checkpointer keeps the conversation state between
requests, and a pending question (which of two Springfields?) is a paused graph that the next
message resumes. By default sessions live in memory -- a restart forgets them; with a
``SqliteChatSaver`` (``CHAT_STORE_PATH``) they are kept in a file and found again after one. Their
number is bounded either way: the least recently used one is dropped first.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections import OrderedDict
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from bike_routing_agent.chat.graph import build_chat_graph
from bike_routing_agent.chat.models import (
    MAX_MESSAGE_CHARS,
    ChatHistory,
    ChatMessage,
    ChatReply,
    Planner,
    Sights,
)
from bike_routing_agent.chat.store import LatestOnlyMemorySaver

logger = logging.getLogger(__name__)

SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
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
        stored = getattr(self._checkpointer, "thread_ids", None)
        for sid in stored() if callable(stored) else ():  # least recently used first
            self._touch(sid)

    @property
    def sessions(self) -> int:
        return len(self._sessions)

    async def send(
        self, message: str, *, session_id: str | None = None, timezone: str | None = None
    ) -> ChatReply:
        text = message.strip()[:MAX_MESSAGE_CHARS]
        sid = session_id if session_id and session_id in self._sessions else uuid.uuid4().hex
        lock = self._touch(sid)
        config = {"configurable": {"thread_id": sid}}
        async with lock:
            try:
                snapshot = await self._graph.aget_state(config)
                if snapshot.next:  # a question is open: this message is its answer
                    result = await self._graph.ainvoke(Command(resume=text), config)
                else:
                    result = await self._graph.ainvoke(
                        {"user_text": text, "timezone": timezone}, config
                    )
            except Exception:  # a turn must never crash the conversation
                logger.exception("chat turn failed")
                return ChatReply(
                    session_id=sid, reply=FAILURE_REPLY, suggestions=["New route"], failed=True
                )
            finally:
                self._keep_latest_only(sid)
                self._save(sid)
        return self._reply(sid, result)

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
            intent=result.get("intent"),
            awaiting="guided" if result.get("asking") else None,
            failed=bool(result.get("failed")),
        )
