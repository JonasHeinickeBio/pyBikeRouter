"""parse_request node.

Deterministic structured input is the primary path: the API layer already
validated a RoutePlanAPIRequest before the graph ever runs, so this node
mostly reshapes that payload into the state's origin/destination/constraints
fields. Natural-language input goes through an injectable `llm_parser`
callable so the LLM extraction step can be swapped or mocked without
touching graph wiring -- it must return the same structured shape, never
coordinates or geometry.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bike_routing_agent.state import RouteAgentState

LLMParser = Callable[[str], dict[str, Any]]
ParseNodeFn = Callable[[RouteAgentState], dict[str, Any]]


def _place_input(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict) and "lon" in value and "lat" in value:
        return {"coordinate": value}
    if isinstance(value, str):
        return {"query": value}
    return None


def build_parse_node(*, llm_parser: LLMParser | None = None) -> ParseNodeFn:
    def parse_request(state: RouteAgentState) -> dict[str, Any]:
        raw = state.get("raw_input", {})

        if "text" in raw and "origin" not in raw:
            if llm_parser is None:
                return {
                    "status": "invalid",
                    "errors": [
                        {
                            "code": "nl_parsing_unavailable",
                            "message": (
                                "free-text requests require an LLM parser, none is configured"
                            ),
                        }
                    ],
                }
            raw = llm_parser(raw["text"])

        via_inputs = [_place_input(v) for v in raw.get("via", [])]
        return {
            "origin_input": _place_input(raw.get("origin")),
            "destination_input": _place_input(raw.get("destination")),
            "via_inputs": [v for v in via_inputs if v is not None],
            "constraints": raw.get("constraints", {}),
            "status": "in_progress",
        }

    return parse_request
