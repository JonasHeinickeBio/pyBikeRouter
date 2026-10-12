"""optimize_candidates node: with an optimisation request, order the scored candidates by how
well they serve the goals and select the best fit (optimizer/engine.py).

Without ``optimize`` in the state this is a pass-through, so the graph stays one straight line.
It runs after scoring (the candidates carry their metrics, scores and duplicate marks) and
before weather and export, which then work on the optimiser's choice. It never fails a plan: if
the optimisation cannot be done the scorer's ranking stands and a note says so.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from bike_routing_agent.models import OptimizeSpec, RouteCandidate, RouteConstraints
from bike_routing_agent.optimizer.engine import optimize
from bike_routing_agent.state import RouteAgentState

logger = logging.getLogger(__name__)

OptimizeNodeFn = Callable[[RouteAgentState], dict[str, Any]]


def build_optimize_node() -> OptimizeNodeFn:
    def optimize_candidates(state: RouteAgentState) -> dict[str, Any]:
        raw_spec = state.get("optimize")
        raw = state.get("candidates", [])
        if not raw_spec or not raw:
            return {}
        try:
            spec = OptimizeSpec.model_validate(raw_spec)
            constraints = RouteConstraints.model_validate(state.get("constraints", {}))
            candidates = [RouteCandidate.model_validate(c) for c in raw]
            outcome = optimize(candidates, spec, constraints)
        except Exception:  # an optimisation is a bonus: the plan itself must still come back
            logger.exception("optimisation failed; keeping the scorer's ranking")
            return {
                "optimization": {
                    "summary": "The optimisation could not be done; this is the usual ranking.",
                    "table": [],
                }
            }
        dumped = [c.model_dump(mode="json") for c in outcome.ordered]
        return {
            "candidates": dumped,
            "selected_candidate": dumped[0],
            "optimization": outcome.report,
        }

    return optimize_candidates
