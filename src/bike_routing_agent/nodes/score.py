"""score_candidates node.

Applies the deterministic scorer to every candidate and picks the best one.
An empty candidate list (should not normally reach this node, but graphs
can be invoked directly in tests) is treated as a structured no-route
failure rather than an index error.
"""

from __future__ import annotations

from typing import Any

from bike_routing_agent.models import RouteCandidate, RouteConstraints
from bike_routing_agent.scoring.basic import score_candidate
from bike_routing_agent.state import RouteAgentState


def score_candidates(state: RouteAgentState) -> dict[str, Any]:
    candidates = state.get("candidates", [])
    if not candidates:
        return {
            "status": "no_route",
            "errors": [{"code": "no_candidates", "message": "no route candidates to score"}],
        }

    constraints = RouteConstraints.model_validate(state.get("constraints", {}))
    scored: list[RouteCandidate] = []
    for candidate_dict in candidates:
        candidate = RouteCandidate.model_validate(candidate_dict)
        score, breakdown = score_candidate(candidate, constraints)
        scored.append(candidate.model_copy(update={"score": score, "score_breakdown": breakdown}))

    best = max(scored, key=lambda c: c.score if c.score is not None else float("-inf"))

    return {
        "status": "in_progress",
        "candidates": [c.model_dump(mode="json") for c in scored],
        "selected_candidate": best.model_dump(mode="json"),
    }
