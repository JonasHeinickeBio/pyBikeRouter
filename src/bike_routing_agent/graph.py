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
from bike_routing_agent.nodes.compare import build_compare_node
from bike_routing_agent.nodes.enrich import build_enrich_node
from bike_routing_agent.nodes.export import build_export_node
from bike_routing_agent.nodes.geocode import build_geocode_node
from bike_routing_agent.nodes.parse import LLMParser, build_parse_node
from bike_routing_agent.nodes.poi_stops import build_poi_stops_node
from bike_routing_agent.nodes.route import build_route_node
from bike_routing_agent.nodes.score import build_score_node
from bike_routing_agent.nodes.validate import validate_request
from bike_routing_agent.nodes.weather import build_weather_node
from bike_routing_agent.poi.service import PoiService
from bike_routing_agent.providers.base import GeocodeProvider, RoutingProvider
from bike_routing_agent.scoring.alternatives import DEFAULT_DEDUP_THRESHOLD_M
from bike_routing_agent.state import RouteAgentState
from bike_routing_agent.storage.artifacts import ArtifactStore
from bike_routing_agent.weather.service import WeatherService

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
    return END if state.get("status") in _TERMINAL_AFTER_GEOCODE else "select_poi_stops"


def _after_route(state: RouteAgentState) -> str:
    return END if state.get("status") in _TERMINAL_AFTER_ROUTE else "enrich_candidates"


def _after_score(state: RouteAgentState) -> str:
    return END if state.get("status") in _TERMINAL_AFTER_SCORE else "weather_candidates"


def build_graph(
    *,
    geocode_provider: GeocodeProvider,
    routing_providers: Sequence[RoutingProvider],
    export_dir: Path | None = None,
    artifact_store: ArtifactStore | None = None,
    llm_parser: LLMParser | None = None,
    ambiguity_margin: float = 0.05,
    min_confidence: float = 0.3,
    checkpointer: BaseCheckpointSaver | None = None,
    surface_enricher: SurfaceEnricher | None = None,
    alternative_dedup_threshold_m: float = DEFAULT_DEDUP_THRESHOLD_M,
    weather_service: WeatherService | None = None,
    weather_max_samples: int = 5,
    weather_spacing_km: float = 10.0,
    weather_option_hours: tuple[int, int] = (0, 0),
    poi_service: PoiService | None = None,
    optional_routing_providers: Sequence[RoutingProvider] = (),
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
    # Only acts when the request asked for famous-POI stops (issue #55); best effort.
    graph.add_node("select_poi_stops", build_poi_stops_node(service=poi_service))
    graph.add_node(
        "route_with_provider",
        build_route_node(
            routing_providers=routing_providers, optional_providers=optional_routing_providers
        ),
    )
    # Always present so routing never talks to scoring's surface assumptions
    # directly; a no-op pass-through when no enricher is configured (issue #3).
    graph.add_node("enrich_candidates", build_enrich_node(surface_enricher=surface_enricher))
    graph.add_node(
        "score_candidates", build_score_node(dedup_threshold_m=alternative_dedup_threshold_m)
    )
    # Best effort and informational: a no-op without a weather service, and it
    # never changes the plan's outcome (nodes/weather.py).
    graph.add_node(
        "weather_candidates",
        build_weather_node(
            service=weather_service,
            max_samples=weather_max_samples,
            spacing_km=weather_spacing_km,
            option_hours_before=weather_option_hours[0],
            option_hours_after=weather_option_hours[1],
        ),
    )
    # Informational: short pros/cons per distinct route (never changes the ranking).
    graph.add_node("annotate_alternatives", build_compare_node())
    graph.add_node(
        "explain_and_export",
        build_export_node(export_dir=export_dir, artifact_store=artifact_store),
    )

    graph.add_edge(START, "parse_request")
    graph.add_conditional_edges("parse_request", _after_parse, ["validate_request", END])
    graph.add_conditional_edges("validate_request", _after_validate, ["geocode_locations", END])
    graph.add_conditional_edges("geocode_locations", _after_geocode, ["select_poi_stops", END])
    graph.add_edge("select_poi_stops", "route_with_provider")
    graph.add_conditional_edges("route_with_provider", _after_route, ["enrich_candidates", END])
    graph.add_edge("enrich_candidates", "score_candidates")
    graph.add_conditional_edges("score_candidates", _after_score, ["weather_candidates", END])
    graph.add_edge("weather_candidates", "annotate_alternatives")
    graph.add_edge("annotate_alternatives", "explain_and_export")
    graph.add_edge("explain_and_export", END)

    return graph.compile(checkpointer=checkpointer)
