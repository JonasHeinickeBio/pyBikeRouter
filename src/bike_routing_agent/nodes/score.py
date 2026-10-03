"""score_candidates node.

Applies the deterministic scorer to every candidate, ranks them (issue #24)
and picks the best one -- rank 1.
An empty candidate list (should not normally reach this node, but graphs
can be invoked directly in tests) is treated as a structured no-route
failure rather than an index error.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bike_routing_agent.models import MAX_ALTERNATIVES, RouteCandidate, RouteConstraints
from bike_routing_agent.scoring.alternatives import DEFAULT_DEDUP_THRESHOLD_M, rank_candidates
from bike_routing_agent.scoring.basic import score_candidate
from bike_routing_agent.state import RouteAgentState

ScoreNodeFn = Callable[[RouteAgentState], dict[str, Any]]


def _alternatives_cap(raw: Any) -> int | None:
    """The request's ``max_alternatives``, or None when absent/unusable.

    The API validates the bounds; graphs invoked directly can pass anything,
    and a nonsensical cap must not turn into an error or an empty result.
    """
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return None
    return min(raw, MAX_ALTERNATIVES)


def build_score_node(*, dedup_threshold_m: float = DEFAULT_DEDUP_THRESHOLD_M) -> ScoreNodeFn:
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
            scored.append(
                candidate.model_copy(update={"score": score, "score_breakdown": breakdown})
            )

        # Rank (and, when a cap was requested, de-duplicate and cap). The
        # selected candidate is rank 1 -- the best-scored member of its
        # cluster -- so the verdict is the same as picking the maximum.
        ranked = rank_candidates(
            scored,
            max_alternatives=_alternatives_cap(state.get("max_alternatives")),
            dedup_threshold_m=dedup_threshold_m,
        )

        return {
            "status": "in_progress",
            "candidates": [c.model_dump(mode="json") for c in ranked],
            "selected_candidate": ranked[0].model_dump(mode="json"),
        }

    return score_candidates


# Default-configured node, kept as a plain function for direct use in tests.
score_candidates = build_score_node()
