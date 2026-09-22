"""geocode_locations node.

Resolves any place given as free text to a coordinate via a GeocodeProvider.
Coordinates supplied directly bypass geocoding entirely. Ambiguous or empty
results become a clarification requirement rather than an arbitrary choice
of place.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from bike_routing_agent.errors import GeocodingNotFoundError, ProviderError
from bike_routing_agent.providers.base import GeocodeProvider
from bike_routing_agent.state import RouteAgentState

_OptDict = dict[str, Any] | None
_ResolveResult = tuple[_OptDict, _OptDict, _OptDict]
GeocodeNodeFn = Callable[[RouteAgentState], Awaitable[dict[str, Any]]]

NO_MATCH_HINT = (
    "no matches found; try a full street name with house number and postcode "
    "(e.g. 'Kasernenstraße 23, 38106 Braunschweig'), or pass a coordinate directly"
)


def _no_match(query: str) -> dict[str, Any]:
    return {"field": query, "candidates": [], "hint": NO_MATCH_HINT}


def _not_found_error(query: str) -> dict[str, Any]:
    return {"code": "geocoding_not_found", "message": f"no results for '{query}'"}


def build_geocode_node(
    *,
    geocode_provider: GeocodeProvider,
    ambiguity_margin: float = 0.05,
    min_confidence: float = 0.3,
    candidate_limit: int = 5,
) -> GeocodeNodeFn:
    async def _resolve(place_input: _OptDict) -> _ResolveResult:
        """Returns (resolved_coordinate, clarification_entry, error_entry)."""
        if place_input is None:
            return None, None, None
        if "coordinate" in place_input:
            return place_input["coordinate"], None, None

        query = place_input["query"]
        try:
            candidates = await geocode_provider.geocode(query, limit=candidate_limit)
        except GeocodingNotFoundError:
            return None, _no_match(query), _not_found_error(query)
        except ProviderError as exc:
            return None, None, exc.to_dict()

        if not candidates:
            return None, _no_match(query), _not_found_error(query)

        confidence_gap = (
            candidates[0].confidence - candidates[1].confidence if len(candidates) > 1 else 1.0
        )
        top_confidence = candidates[0].confidence
        is_ambiguous = top_confidence < min_confidence or confidence_gap < ambiguity_margin
        if is_ambiguous:
            return (
                None,
                {
                    "field": query,
                    "candidates": [c.model_dump(mode="json") for c in candidates],
                },
                None,
            )

        return candidates[0].coordinate.model_dump(mode="json"), None, None

    async def geocode_locations(state: RouteAgentState) -> dict[str, Any]:
        origin_coord, origin_clarify, origin_error = await _resolve(state.get("origin_input"))
        dest_coord, dest_clarify, dest_error = await _resolve(state.get("destination_input"))

        via_coords: list[dict[str, Any]] = []
        via_clarify: list[dict[str, Any]] = []
        via_errors: list[dict[str, Any]] = []
        for via_input in state.get("via_inputs", []):
            coord, clarify, error = await _resolve(via_input)
            if coord is not None:
                via_coords.append(coord)
            if clarify is not None:
                via_clarify.append(clarify)
            if error is not None:
                via_errors.append(error)

        clarification = [c for c in (origin_clarify, dest_clarify) if c is not None] + via_clarify
        errors = [e for e in (origin_error, dest_error) if e is not None] + via_errors

        if clarification:
            return {
                "status": "awaiting_clarification",
                "clarification": clarification,
                "errors": errors,
            }
        if errors:
            return {"status": "provider_failure", "errors": errors}

        # Loop contract (issue #5): no destination is a request to come back
        # to the resolved origin, not a place to geocode -- snapping here
        # means clarification and provenance speak about one origin only.
        if dest_coord is None and state.get("constraints", {}).get("return_to_origin"):
            dest_coord = origin_coord

        return {
            "status": "in_progress",
            "resolved_origin": origin_coord,
            "resolved_destination": dest_coord,
            "resolved_via": via_coords,
        }

    return geocode_locations
