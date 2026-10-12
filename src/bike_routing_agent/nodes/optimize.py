"""optimize_candidates node: with an optimisation request, order the scored candidates by how
well they serve the goals and select the best fit (optimizer/engine.py).

Without ``optimize`` in the state this is a pass-through, so the graph stays one straight line.
It runs after scoring (the candidates carry their metrics, scores and duplicate marks) and
before weather and export, which then work on the optimiser's choice. It never fails a plan: if
the optimisation cannot be done the scorer's ranking stands and a note says so.

For the ``pois`` goal the node counts the well-known places near every candidate, from ONE
search over their merged corridors (poi/service.py ``along_routes``); without a POI service, or
when the lookup fails, that goal has no data and is dropped and reported.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from bike_routing_agent.errors import ProviderError
from bike_routing_agent.models import OptimizeSpec, RouteCandidate, RouteConstraints
from bike_routing_agent.optimizer.engine import optimize
from bike_routing_agent.poi.service import PoiService
from bike_routing_agent.state import RouteAgentState

logger = logging.getLogger(__name__)

OptimizeNodeFn = Callable[[RouteAgentState], Awaitable[dict[str, Any]]]
MAX_ROUTES_FOR_POIS = 12  # more routes than this do not get counted (the rest: no data)
MAX_NAMED_POIS = 12


def _line(candidate: RouteCandidate) -> list[tuple[float, float]]:
    geometry = candidate.geometry_geojson
    parts = geometry.get("coordinates") or []
    if geometry.get("type") == "MultiLineString":
        points = [p for part in parts for p in part]
    else:
        points = parts
    return [(float(p[0]), float(p[1])) for p in points if len(p) >= 2]


async def count_pois(
    candidates: list[RouteCandidate], spec: OptimizeSpec, service: PoiService | None
) -> tuple[list[RouteCandidate], dict[str, Any]]:
    """The candidates with ``metrics.poi_count`` (and the names in their provenance) set, and
    how the lookup went."""
    if service is None:
        return candidates, {"status": "unavailable"}
    options = spec.poi
    indices = [i for i, c in enumerate(candidates) if len(_line(c)) >= 2][:MAX_ROUTES_FOR_POIS]
    if not indices:
        return candidates, {"status": "unavailable"}
    result = None
    # A search over many corridors is a big one for the public Overpass instance (it answers
    # with 504): when it fails, ask again for the leading routes only -- the rest get no count
    # and are therefore at a disadvantage, which is said (``counted``).
    for subset in (indices, indices[:4]):
        try:
            result = await service.along_routes(
                [_line(candidates[i]) for i in subset],
                options.categories,
                buffer_m=options.corridor_m,
                linked_only=True,
            )
            indices = subset
            break
        except (ProviderError, ValueError) as exc:
            logger.warning("optimizer POI lookup over %d routes failed: %s", len(subset), exc)
        if len(indices) <= 4:
            break
    if result is None:
        return candidates, {"status": "unavailable"}
    updated = list(candidates)
    for i, near in zip(indices, result.per_route, strict=True):
        famous = [p for p in near if p.fame is not None and p.fame >= options.min_fame]
        named = [p.name or p.category for p in famous][:MAX_NAMED_POIS]
        c = candidates[i]
        updated[i] = c.model_copy(
            update={
                "metrics": c.metrics.model_copy(update={"poi_count": len(famous)}),
                "provenance": {**c.provenance, "optimizer_pois": named},
            }
        )
    return updated, {
        "status": result.fame_status,
        "truncated": result.truncated,
        "counted": len(indices),
        "of": len([c for c in candidates if len(_line(c)) >= 2]),
    }


def build_optimize_node(*, poi_service: PoiService | None = None) -> OptimizeNodeFn:
    async def optimize_candidates(state: RouteAgentState) -> dict[str, Any]:
        raw_spec = state.get("optimize")
        raw = state.get("candidates", [])
        if not raw_spec or not raw:
            return {}
        try:
            spec = OptimizeSpec.model_validate(raw_spec)
            constraints = RouteConstraints.model_validate(state.get("constraints", {}))
            candidates = [RouteCandidate.model_validate(c) for c in raw]
            poi_info: dict[str, Any] | None = None
            if any(g.metric == "pois" for g in spec.goals):
                candidates, poi_info = await count_pois(candidates, spec, poi_service)
            outcome = optimize(candidates, spec, constraints)
        except Exception:  # an optimisation is a bonus: the plan itself must still come back
            logger.exception("optimisation failed; keeping the scorer's ranking")
            return {
                "optimization": {
                    "summary": "The optimisation could not be done; this is the usual ranking.",
                    "table": [],
                }
            }
        report = outcome.report
        if poi_info is not None:
            report["poi_status"] = poi_info.get("status")
            report["poi_corridor_m"] = spec.poi.corridor_m
            report["poi_routes_counted"] = poi_info.get("counted")
            report["pois"] = outcome.ordered[0].provenance.get("optimizer_pois", [])
            report["via_place_routes"] = sum(
                1 for c in outcome.ordered if c.provenance.get("optimizer_via")
            )
        dumped = [c.model_dump(mode="json") for c in outcome.ordered]
        return {
            "candidates": dumped,
            "selected_candidate": dumped[0],
            "optimization": report,
        }

    return optimize_candidates
