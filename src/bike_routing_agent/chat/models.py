"""Types of the chat layer: the conversation state, what a turn returns, what it needs."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Protocol, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_MESSAGE_CHARS = 500

Awaiting = Literal["place_choice", "guided"]


class PlanError(Exception):
    """The request the chat built could not be planned as it is (the API would answer 422);
    the message is meant for the person chatting."""


class TextUnavailableError(Exception):
    """Free-text planning (the LLM parser) is not configured."""


class SightsUnavailableError(Exception):
    """The lookup of sights failed (map data service busy, switched off ...)."""


class Planner(Protocol):
    """Plans a route: the same machinery as ``POST /v1/route/plan`` (history, fallbacks ...)."""

    async def plan(self, request: dict[str, Any]) -> dict[str, Any]: ...

    async def plan_text(self, text: str, timezone: str | None) -> dict[str, Any]: ...


class Sights(Protocol):
    """Well-known places along a route line (``[lon, lat]`` pairs), best known first."""

    async def along(self, line: list[list[float]], limit: int) -> list[dict[str, Any]]: ...


class ChatState(TypedDict, total=False):
    """Everything a conversation remembers (kept by the LangGraph checkpointer per session)."""

    messages: Annotated[list[AnyMessage], add_messages]
    user_text: str
    timezone: str | None
    intent: str | None
    # Step-by-step dialogue: what was answered so far, and which question is open.
    draft: dict[str, Any]
    asking: str | None
    # The request behind the last route, and the route itself (a RoutePlanResponse as a dict).
    last_request: dict[str, Any] | None
    last_plan: dict[str, Any] | None
    # The request that could not be planned last (so "use openrouteservice" can retry exactly it).
    failed_request: dict[str, Any] | None
    failed: bool  # this turn could not produce the route that was asked for
    awaiting_request: dict[str, Any] | None  # waits for a place choice; not tied to last_plan
    # A request waiting to be planned, and a short note on what changed to get there.
    pending_request: dict[str, Any] | None
    change_note: str
    # Places that were ambiguous and wait for the person to choose.
    clarification: list[dict[str, Any]]
    clarify_result: str | None
    # What this turn produced (reset at the start of every turn).
    reply: str
    suggestions: list[str]
    plan: dict[str, Any] | None
    focus_rank: int | None


class ChatReply(BaseModel):
    session_id: str
    reply: str
    # Short messages the person can tap instead of typing.
    suggestions: list[str] = Field(default_factory=list)
    # A plan to draw on the map this turn (a new route, or the alternatives of the last one).
    plan: dict[str, Any] | None = None
    # With ``plan``: the alternative (by rank) that was asked for.
    focus_rank: int | None = None
    intent: str | None = None
    awaiting: Awaiting | None = None
    # The turn could not do what was asked (no route, a failed lookup); a question back is not.
    failed: bool = False


class ChatRequest(BaseModel):
    """Body of ``POST /v1/chat``."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    # From the first reply; omitted (or unknown) starts a new conversation.
    session_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    # The user's IANA time zone, for "tomorrow at 8" in free text.
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {value!r}") from exc
        return value
