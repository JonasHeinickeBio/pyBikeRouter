"""Typed LangGraph state.

Every field is JSON-serializable (plain dict/list/str/float/bool) apart from
`messages`, which carries LangChain messages for the LLM-assisted parse step
and any future clarification dialogue. Domain objects (Coordinate,
RouteCandidate, ...) are stored as `.model_dump(mode="json")` dicts and
rehydrated with `.model_validate(...)` inside node functions -- state itself
never holds a pydantic instance.

Nodes must return partial updates (only the keys they change), not the
whole state, and must not mutate the `state` mapping they receive.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

PlanStatus = Literal[
    "in_progress",
    "invalid",
    "awaiting_clarification",
    "provider_failure",
    "no_route",
    "ready",
]


class RouteAgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]

    raw_input: dict[str, Any]

    origin_input: dict[str, Any] | None
    destination_input: dict[str, Any] | None
    via_inputs: list[dict[str, Any]]
    constraints: dict[str, Any]
    # Cap on distinct alternatives (issue #24); None keeps every candidate.
    max_alternatives: int | None
    # ISO-8601 UTC departure for the weather forecast (None = now).
    departure_time: str | None
    weather_status: str | None
    # Famous-POI route stops (issue #55): the request (validated PoiStopsRequest as a dict),
    # the POIs chosen as extra via points, and how the selection went.
    poi_stops_request: dict[str, Any] | None
    # Engines to use for this request instead of the configured ones (None = configured).
    routing_engines: list[str] | None
    poi_stops: list[dict[str, Any]] | None
    poi_stops_status: str | None
    # What a free-text parser understood (issue #30): the structured request,
    # its notes about anything unclear, and which model/prompt produced it.
    interpretation: dict[str, Any] | None

    resolved_origin: dict[str, Any] | None
    resolved_destination: dict[str, Any] | None
    resolved_via: list[dict[str, Any]]
    # Provenance of synthesized loop waypoints (issue #5): None for normal
    # requests and for loops whose shape came from caller-supplied vias.
    loop_plan: dict[str, Any] | None

    candidates: list[dict[str, Any]]
    selected_candidate: dict[str, Any] | None

    status: PlanStatus
    errors: list[dict[str, Any]]
    clarification: list[dict[str, Any]]
    # Id shared by the exported artifacts and the history record (issue #7).
    route_id: str
    explanation: str | None
    artifacts: dict[str, str]
