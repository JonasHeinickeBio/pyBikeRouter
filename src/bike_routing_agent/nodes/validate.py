"""validate_request node.

Re-validates constraints with Pydantic (defense in depth for callers that
invoke the graph directly, bypassing the API layer) and checks for missing
origin/destination. Both failure modes are terminal for this run but are
distinguished by status: malformed constraints are `invalid`, missing
places are `awaiting_clarification` since the caller can supply them.

Exception: a loop request (`return_to_origin`, issue #5) legitimately has
no destination -- that check is skipped for it, and its sizing requirement
(target_distance_km) is enforced by the constraints model itself.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from bike_routing_agent.models import RouteConstraints
from bike_routing_agent.state import RouteAgentState


def validate_request(state: RouteAgentState) -> dict[str, Any]:
    if state.get("status") == "invalid":
        return {}

    try:
        constraints = RouteConstraints.model_validate(state.get("constraints", {}))
    except ValidationError as exc:
        return {
            "status": "invalid",
            "errors": [
                {"code": "invalid_constraints", "message": str(err["msg"]), "loc": list(err["loc"])}
                for err in exc.errors()
            ],
        }

    # A loop request is single-origin by contract (issue #5): the missing
    # destination is the point, not an omission -- the geocode node snaps it
    # back onto the origin. Loop sizing is enforced by RouteConstraints.
    is_loop = constraints.return_to_origin

    place_inputs: list[tuple[str, Any]] = [("origin", state.get("origin_input"))]
    if not is_loop:
        place_inputs.append(("destination", state.get("destination_input")))
    missing = [field for field, value in place_inputs if value is None]
    if missing:
        return {
            "status": "awaiting_clarification",
            "clarification": [{"field": field, "candidates": []} for field in missing],
            "errors": [
                {"code": "missing_place", "message": f"{field} was not provided", "loc": [field]}
                for field in missing
            ],
        }

    return {"status": "in_progress"}
