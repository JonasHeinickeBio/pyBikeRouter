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

import logging
from collections.abc import Callable
from typing import Any

from bike_routing_agent.llm.parser import LLMParseError
from bike_routing_agent.state import RouteAgentState

logger = logging.getLogger(__name__)

# (text) -> request dict; an optional ``timezone=`` keyword gives the user's IANA zone.
LLMParser = Callable[..., dict[str, Any]]
ParseNodeFn = Callable[[RouteAgentState], dict[str, Any]]


def _place_input(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict) and "lon" in value and "lat" in value:
        return {"coordinate": value}
    if isinstance(value, str):
        return {"query": value}
    return None


def _invalid(code: str, message: str) -> dict[str, Any]:
    return {"status": "invalid", "errors": [{"code": code, "message": message}]}


def _llm_output_problem(parsed: Any) -> str | None:
    """Why a free-text parser's output is not acceptable, or ``None``.

    The LLM contract is words in, a request out -- never coordinates. The
    adapter enforces it, and this check enforces it again at the graph boundary
    so any injected parser (or a future one) is held to the same rule: places
    must be plain strings, not coordinate objects.
    """
    if not isinstance(parsed, dict):
        return "the parser must return an object"
    origin = parsed.get("origin")
    if not isinstance(origin, str) or not origin.strip():
        return "origin must be a place name"
    destination = parsed.get("destination")
    if destination is not None and not isinstance(destination, str):
        return "destination must be a place name or null"
    via = parsed.get("via", [])
    if not isinstance(via, list) or not all(isinstance(v, str) and v.strip() for v in via):
        return "via must be a list of place names"
    if not isinstance(parsed.get("constraints", {}), dict):
        return "constraints must be an object"
    return None


def build_parse_node(*, llm_parser: LLMParser | None = None) -> ParseNodeFn:
    def parse_request(state: RouteAgentState) -> dict[str, Any]:
        original = state.get("raw_input", {})
        raw = original
        interpretation: dict[str, Any] | None = None

        if "text" in original and "origin" not in original:
            if llm_parser is None:
                return _invalid(
                    "nl_parsing_unavailable",
                    "free-text requests require an LLM parser, none is configured",
                )
            timezone = original.get("timezone")
            try:
                raw = (
                    llm_parser(original["text"], timezone=timezone)
                    if timezone
                    else llm_parser(original["text"])
                )
            except LLMParseError as exc:
                return _invalid(exc.code, exc.message)
            except Exception:
                logger.exception("free-text parser failed")
                return _invalid("llm_parser_error", "the free-text parser failed")
            problem = _llm_output_problem(raw)
            if problem is not None:
                return _invalid("llm_parser_invalid_output", problem)
            interpretation = {
                "request": {k: raw.get(k) for k in ("origin", "destination", "via", "constraints")},
                "departure_time": raw.get("departure_time"),
                "notes": list(raw.get("notes", [])),
                "parser": raw.get("provenance"),
            }

        via_inputs = [_place_input(v) for v in raw.get("via", [])]
        update: dict[str, Any] = {
            "origin_input": _place_input(raw.get("origin")),
            "destination_input": _place_input(raw.get("destination")),
            "via_inputs": [v for v in via_inputs if v is not None],
            "constraints": raw.get("constraints", {}),
            # Caps come from the API request, never from what a parser produced.
            "max_alternatives": original.get("max_alternatives"),
            "poi_stops_request": original.get("poi_stops"),
            "departure_time": raw.get("departure_time"),
            "status": "in_progress",
        }
        if interpretation is not None:
            update["interpretation"] = interpretation
        return update

    return parse_request
