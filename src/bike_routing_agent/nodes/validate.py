"""validate_request node.

Re-validates constraints with Pydantic (defense in depth for callers that
invoke the graph directly, bypassing the API layer) and checks for missing
origin/destination. Both failure modes are terminal for this run but are
distinguished by status: malformed constraints are `invalid`, missing
places are `awaiting_clarification` since the caller can supply them.
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
        RouteConstraints.model_validate(state.get("constraints", {}))
    except ValidationError as exc:
        return {
            "status": "invalid",
            "errors": [
                {"code": "invalid_constraints", "message": str(err["msg"]), "loc": list(err["loc"])}
                for err in exc.errors()
            ],
        }

    missing = [
        field
        for field, value in (
            ("origin", state.get("origin_input")),
            ("destination", state.get("destination_input")),
        )
        if value is None
    ]
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
