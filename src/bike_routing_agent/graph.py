"""LangGraph workflow wiring.

The graph never calls a routing provider unless validation and geocoding
both succeeded, and it always ends in one of the terminal `status` values
defined on RouteAgentState -- there is no path that falls off the end of
the graph without a status.
"""

# mypy: disable-error-code="call-overload, arg-type"
# StateGraph.add_node's overloads are written for its own node/runnable
# signatures and don't line up with plain `Callable[[RouteAgentState], ...]`
# closures, even though they work correctly at runtime.

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from bike_routing_agent.enrichment.base import SurfaceEnricher
from bike_routing_agent.nodes.enrich import build_enrich_node
from bike_routing_agent.nodes.export import build_export_node
from bike_routing_agent.nodes.geocode import build_geocode_node
from bike_routing_agent.nodes.parse import LLMParser, build_parse_node
from bike_routing_agent.nodes.route import build_route_node
from bike_routing_agent.nodes.score import score_candidates
from bike_routing_agent.nodes.validate import validate_request
from bike_routing_agent.providers.base import GeocodeProvider, RoutingProvider
from bike_routing_agent.state import RouteAgentState

_TERMINAL_AFTER_PARSE = {"invalid"}
_TERMINAL_AFTER_VALIDATE = {"invalid", "awaiting_clarification"}
_TERMINAL_AFTER_GEOCODE = {"awaiting_clarification", "provider_failure"}
_TERMINAL_AFTER_ROUTE = {"no_route", "provider_failure"}
_TERMINAL_AFTER_SCORE = {"no_route"}


def _after_parse(state: RouteAgentState) -> str:
    return END if state.get("status") in _TERMINAL_AFTER_PARSE else "validate_request"


def _after_validate(state: RouteAgentState) -> str:
    return END if state.get("status") in _TERMINAL_AFTER_VALIDATE else "geocode_locations"


def _after_geocode(state: RouteAgentState) -> str:
    return END if state.get("status") in _TERMINAL_AFTER_GEOCODE else "route_with_provider"


def _after_route(state: RouteAgentState) -> str:
    return END if state.get("status") in _TERMINAL_AFTER_ROUTE else "enrich_candidates"


def _after_score(state: RouteAgentState) -> str:
    return END if state.get("status") in _TERMINAL_AFTER_SCORE else "explain_and_export"


def build_graph(
    *,
    geocode_provider: GeocodeProvider,
    routing_providers: Sequence[RoutingProvider],
    export_dir: Path,
    llm_parser: LLMParser | None = None,
    ambiguity_margin: float = 0.05,
    min_confidence: float = 0.3,
    checkpointer: BaseCheckpointSaver | None = None,
    surface_enricher: SurfaceEnricher | None = None,
) -> Any:
    graph = StateGraph(RouteAgentState)

    graph.add_node("parse_request", build_parse_node(llm_parser=llm_parser))
    graph.add_node("validate_request", validate_request)
    graph.add_node(
        "geocode_locations",
        build_geocode_node(
            geocode_provider=geocode_provider,
            ambiguity_margin=ambiguity_margin,
            min_confidence=min_confidence,
        ),
    )
    graph.add_node("route_with_provider", build_route_node(routing_providers=routing_providers))
    # Always present so routing never talks to scoring's surface assumptions
    # directly; a no-op pass-through when no enricher is configured (issue #3).
    graph.add_node("enrich_candidates", build_enrich_node(surface_enricher=surface_enricher))
    graph.add_node("score_candidates", score_candidates)
    graph.add_node("explain_and_export", build_export_node(export_dir=export_dir))

    graph.add_edge(START, "parse_request")
    graph.add_conditional_edges("parse_request", _after_parse, ["validate_request", END])
    graph.add_conditional_edges("validate_request", _after_validate, ["geocode_locations", END])
    graph.add_conditional_edges("geocode_locations", _after_geocode, ["route_with_provider", END])
    graph.add_conditional_edges("route_with_provider", _after_route, ["enrich_candidates", END])
    graph.add_edge("enrich_candidates", "score_candidates")
    graph.add_conditional_edges("score_candidates", _after_score, ["explain_and_export", END])
    graph.add_edge("explain_and_export", END)

    return graph.compile(checkpointer=checkpointer)
