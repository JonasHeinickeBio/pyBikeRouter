"""annotate_alternatives node: short pros and cons per distinct route.

Runs after weather (so rain and wind can be compared too) and before the
explanation. Purely informational: it never changes the ranking, the selected
route or the plan's status.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bike_routing_agent.models import RouteCandidate, RouteConstraints
from bike_routing_agent.scoring.pros_cons import annotate_pros_cons
from bike_routing_agent.state import RouteAgentState

CompareNodeFn = Callable[[RouteAgentState], dict[str, Any]]


def build_compare_node() -> CompareNodeFn:
    def annotate_alternatives(state: RouteAgentState) -> dict[str, Any]:
        raw = state.get("candidates", [])
        if len(raw) < 2:
            return {}
        constraints = RouteConstraints.model_validate(state.get("constraints", {}))
        annotated = annotate_pros_cons([RouteCandidate.model_validate(c) for c in raw], constraints)
        update: dict[str, Any] = {"candidates": [c.model_dump(mode="json") for c in annotated]}
        selected = state.get("selected_candidate")
        if selected is not None:
            index = next((i for i, c in enumerate(raw) if c == selected), 0)
            update["selected_candidate"] = update["candidates"][index]
        return update

    return annotate_alternatives
