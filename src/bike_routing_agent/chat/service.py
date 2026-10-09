"""``ChatService``: sessions on top of the chat graph.

A session is a LangGraph *thread*: the checkpointer keeps the conversation state between
requests, and a pending question (which of two Springfields?) is a paused graph that the next
message resumes. Sessions live in memory (the default checkpointer) -- a restart forgets them --
and their number is bounded: the least recently used one is dropped first.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections import OrderedDict
from collections.abc import Sequence
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from bike_routing_agent.chat.graph import build_chat_graph
from bike_routing_agent.chat.models import (
    MAX_MESSAGE_CHARS,
    ChatReply,
    Planner,
    Sights,
)

logger = logging.getLogger(__name__)

SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
FAILURE_REPLY = (
    "Something went wrong while planning that. Please try again, or rephrase "
    "('from A to B', '40 km loop from A')."
)


class LatestOnlyMemorySaver(MemorySaver):
    """``MemorySaver`` that can drop a thread's history and keep only its latest checkpoint.

    LangGraph writes a checkpoint per step, each holding the plan (route geometries): without
    pruning, one long conversation grows the process without limit. Resuming a paused question
    needs only the latest checkpoint and its pending writes, so those are kept.
    """

    def prune(self, thread_ids: Sequence[str], *, strategy: str = "keep_latest") -> None:
        if strategy != "keep_latest":
            raise NotImplementedError(strategy)
        for thread_id in thread_ids:
            self._keep_latest(thread_id)

    def _keep_latest(self, thread_id: str) -> None:
        for namespace, checkpoints in self.storage.get(thread_id, {}).items():
            if len(checkpoints) <= 1:
                continue
            latest = max(checkpoints)  # checkpoint ids sort in creation order
            versions = self.serde.loads_typed(checkpoints[latest][0]).get("channel_versions") or {}
            for checkpoint_id in [c for c in checkpoints if c != latest]:
                del checkpoints[checkpoint_id]
                self.writes.pop((thread_id, namespace, checkpoint_id), None)
            kept = {(thread_id, namespace, channel, v) for channel, v in versions.items()}
            for key in [k for k in self.blobs if k[:2] == (thread_id, namespace) and k not in kept]:
                del self.blobs[key]


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
        return self._reply(sid, result)

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
