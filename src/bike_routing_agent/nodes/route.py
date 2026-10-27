"""route_with_provider node.

Builds an engine-neutral RoutingRequest from resolved coordinates and calls
every configured routing provider **in parallel**, forwarding all resulting
candidates to the scoring step (issue #2: multiple engines are evaluated
side by side and the best-scoring candidate wins).

Provider failures become structured state, never uncaught exceptions --
`no_route` (no path exists) is distinguished from `provider_failure`
(timeout, rate limit, bad response) so callers can react differently. One
provider failing does not sink the request: as long as any provider returns
a candidate the run proceeds and the failures are recorded alongside.

Loop requests (issue #5) get their circuit synthesized through
`loops.synthesize_loop_vias` unless the caller already shaped it with vias;
the plan is recorded in state as provenance for the explanation.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from bike_routing_agent.errors import ProviderCoverageError, ProviderError, ProviderNoRouteError
from bike_routing_agent.loops import (
    LOOP_BEARINGS_DEG,
    LOOP_MAX_ROUTES,
    LOOP_TOLERANCE,
    LoopPlan,
    next_chord_target,
    synthesize_loop_vias,
)
from bike_routing_agent.models import (
    Coordinate,
    OptimizeSpec,
    RouteConstraints,
    RoutingRequest,
)
from bike_routing_agent.poi.service import PoiService
from bike_routing_agent.providers.base import RoutingProvider
from bike_routing_agent.providers.brouter_optimize import PoiVia
from bike_routing_agent.providers.brouter_segments import SEGMENTS_BASE_URL, segments_for_points
from bike_routing_agent.state import RouteAgentState

logger = logging.getLogger(__name__)

NodeFn = Callable[[RouteAgentState], Awaitable[dict[str, Any]]]

# Hard cap on any provider's alternatives, whatever it does internally: they are a
# bonus and must never hold back a route that already came back.
ALTERNATIVES_DEADLINE_S = 12.0


POI_VIA_CORRIDORS_M = (3000.0, 1500.0)  # how far off the direct line a place may be; a smaller
# corridor is the retry when the first search failed (public Overpass answers big ones with 504)
MAX_POI_VIA_CANDIDATES = 3


async def _poi_vias(
    service: PoiService | None, request: RoutingRequest, spec: OptimizeSpec
) -> list[PoiVia]:
    """Well-known places near the direct line, most famous first: routes through them are what
    makes the ``pois`` goal reachable (the profiles alone follow nearly the same corridor)."""
    if service is None or request.constraints.return_to_origin or request.via:
        return []
    if not any(goal.metric == "pois" for goal in spec.goals):
        return []
    line = [
        (request.origin.lon, request.origin.lat),
        (request.destination.lon, request.destination.lat),
    ]
    if line[0] == line[1]:
        return []
    found = None
    for buffer_m in POI_VIA_CORRIDORS_M:
        try:
            found = await service.along_route(
                line, spec.poi.categories, buffer_m=buffer_m, linked_only=True
            )
            break
        except Exception:  # best effort: try the smaller corridor, then give up quietly
            continue
    if found is None:
        return []
    famous = [p for p in found.pois if p.fame is not None and p.fame >= spec.poi.min_fame]
    return [
        PoiVia(
            name=p.name or p.category,
            coordinate=Coordinate(lon=p.lon, lat=p.lat),
            along_km=p.along_route_km or 0.0,
        )
        for p in famous[:MAX_POI_VIA_CANDIDATES]
    ]


async def _optimization_candidates(
    provider: Any,
    request: RoutingRequest,
    spec: OptimizeSpec,
    poi_vias: Sequence[PoiVia] = (),
) -> list[Any]:
    """The provider's many routes for an optimisation (best effort, own deadline)."""
    cap = ALTERNATIVES_DEADLINE_S
    own = getattr(provider, "optimization_deadline_s", None)
    if callable(own):
        cap = max(cap, float(own(request)) + 5.0)
    try:
        kwargs = {"poi_vias": poi_vias} if poi_vias else {}
        return list(
            await asyncio.wait_for(provider.optimization_candidates(request, spec, **kwargs), cap)
        )
    except Exception:  # best effort, like the alternatives
        return []


async def _bounded_alternatives(provider: Any, request: RoutingRequest) -> list[Any]:
    cap = ALTERNATIVES_DEADLINE_S
    own = getattr(provider, "alternatives_deadline_s", None)  # a provider whose time grows
    if callable(own):  # with the trip names its own deadline; the cap only lets it finish
        cap = max(cap, float(own(request)) + 5.0)
    try:
        return list(await asyncio.wait_for(provider.alternatives(request), cap))
    except Exception:  # best effort: timeouts and failures just mean no extras
        return []


def _coverage_error(
    error: ProviderCoverageError, request: RoutingRequest, providers: Sequence[RoutingProvider]
) -> dict[str, Any]:
    """The coverage error with what a client needs to put the choice to the user: which map
    tiles the trip touches (the engine reports only the first one it lacks), where the official
    files are, and which engines are already configured."""
    points = [request.origin, *request.via, request.destination]
    reported = list(error.detail.get("segments", []))
    touched = segments_for_points((p.lon, p.lat) for p in points)
    as_dict = error.to_dict()
    as_dict["detail"] = {
        **error.detail,
        "segments": [*reported, *(s for s in touched if s not in reported)],
        "reported_missing": reported,
        "download_base": SEGMENTS_BASE_URL,
        "configured_engines": [p.name for p in providers],
    }
    return as_dict


def _engines_for(
    requested: Sequence[str] | None,
    configured: Sequence[RoutingProvider],
    optional: Sequence[RoutingProvider],
) -> Sequence[RoutingProvider]:
    """The engines for one request: the ones it names (among the configured and the optional
    ones the server offers), or the configured ones when it names none that exist."""
    if not requested:
        return configured
    by_name = {p.name: p for p in [*optional, *configured]}  # configured wins a name clash
    chosen = [by_name[name] for name in dict.fromkeys(requested) if name in by_name]
    return chosen or configured


async def _sized_loop(
    provider: RoutingProvider,
    origin: Coordinate,
    destination: Coordinate,
    constraints: RouteConstraints,
    goal_m: float,
) -> LoopPlan | None:
    """A loop whose *road* distance is near ``goal_m``, found by routing it.

    A loop drawn with chords of the target length comes out 25-45 % longer on real roads, so the
    first route is measured and the circuit redrawn by the ratio (at most ``LOOP_MAX_ROUTES``
    requests). When an engine cannot reach a waypoint the circuit is turned around the start and
    tried again. ``None`` when no attempt got a route: the ordinary request then reports why.
    """
    chord_m = goal_m
    turns = 0
    best: tuple[float, LoopPlan] | None = None
    for attempt in range(1, LOOP_MAX_ROUTES + 1):
        plan = synthesize_loop_vias(
            origin,
            chord_m,
            direction=constraints.loop_direction,
            bearing_deg=LOOP_BEARINGS_DEG[turns % len(LOOP_BEARINGS_DEG)],
        )
        request = RoutingRequest(
            origin=origin, destination=destination, via=list(plan.vias), constraints=constraints
        )
        try:
            candidate = await provider.route(request)
        except ProviderCoverageError:
            return None  # turning the loop does not bring the map data
        except ProviderError:
            turns += 1  # a waypoint the engine cannot reach: another direction
            continue
        except Exception:  # sizing is best effort; the ordinary request reports real trouble
            logger.warning("loop sizing stopped", exc_info=True)
            break
        routed_m = float(candidate.metrics.distance_m or 0.0)
        if routed_m <= 0:
            break
        miss = abs(routed_m - goal_m) / goal_m
        sized = dataclasses.replace(plan, routed_m=routed_m, attempts=attempt)
        if best is None or miss < best[0]:
            best = (miss, sized)
        if miss <= LOOP_TOLERANCE:
            break
        chord_m = next_chord_target(chord_m, routed_m, goal_m)
    return best[1] if best else None


def build_route_node(
    *,
    routing_providers: Sequence[RoutingProvider],
    optional_providers: Sequence[RoutingProvider] = (),
    poi_service: PoiService | None = None,
) -> NodeFn:
    if not routing_providers:
        raise ValueError("build_route_node requires at least one routing provider")
    configured_providers = routing_providers

    async def route_with_provider(state: RouteAgentState) -> dict[str, Any]:
        routing_providers = _engines_for(
            state.get("routing_engines"), configured_providers, optional_providers
        )
        constraints = RouteConstraints.model_validate(state.get("constraints", {}))
        origin = Coordinate.model_validate(state["resolved_origin"])
        via = [Coordinate.model_validate(v) for v in state.get("resolved_via", [])]

        # Loop contract (issue #5): geocode has already snapped the
        # destination onto the origin. A caller who supplied vias drew the
        # loop themselves and gets exactly that shape; otherwise the circuit
        # is synthesized around the origin at the requested size (and sized
        # against a real route, see _sized_loop), because origin == destination
        # alone would ask the engines for a zero-length route.
        # The sight stops of a loop (select_poi_stops) draw the circuit themselves.
        loop_plan: dict[str, Any] | None = state.get("loop_plan")
        if constraints.return_to_origin and not via:
            assert constraints.target_distance_km is not None  # RouteConstraints validator
            goal_m = constraints.target_distance_km * 1000.0
            plan = await _sized_loop(
                routing_providers[0],
                origin,
                Coordinate.model_validate(state["resolved_destination"]),
                constraints,
                goal_m,
            ) or synthesize_loop_vias(origin, goal_m, direction=constraints.loop_direction)
            via = list(plan.vias)
            loop_plan = plan.to_state()

        request = RoutingRequest(
            origin=origin,
            destination=Coordinate.model_validate(state["resolved_destination"]),
            via=via,
            constraints=constraints,
        )

        # Engines that can price the same trip differently (BRouter profiles) add
        # alternatives; they run alongside the main requests, are best effort and
        # never turn into errors. An optimisation asks for many more routes instead.
        raw_spec = state.get("optimize")
        spec = OptimizeSpec.model_validate(raw_spec) if raw_spec else None

        poi_vias: list[PoiVia] = (
            await _poi_vias(poi_service, request, spec) if spec is not None else []
        )

        def extra_routes(provider: Any) -> Any:
            if spec is not None and hasattr(provider, "optimization_candidates"):
                return _optimization_candidates(provider, request, spec, poi_vias)
            return _bounded_alternatives(provider, request)

        results, extras = await asyncio.gather(
            asyncio.gather(
                *(provider.route(request) for provider in routing_providers),
                return_exceptions=True,
            ),
            asyncio.gather(
                *(
                    extra_routes(provider)
                    for provider in routing_providers
                    if hasattr(provider, "alternatives")
                    or (spec is not None and hasattr(provider, "optimization_candidates"))
                ),
            ),
        )
        candidates: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        no_route_only = True
        for provider, result in zip(routing_providers, results, strict=True):
            if isinstance(result, BaseException) and not isinstance(result, Exception):
                raise result
            if isinstance(result, ProviderCoverageError):
                no_route_only = False
                errors.append(_coverage_error(result, request, routing_providers))
            elif isinstance(result, ProviderNoRouteError):
                errors.append(result.to_dict())
            elif isinstance(result, ProviderError):
                no_route_only = False
                errors.append(result.to_dict())
            elif isinstance(result, Exception):
                no_route_only = False
                errors.append(
                    ProviderError(
                        f"unexpected {type(result).__name__}: {result}",
                        provider=provider.name,
                    ).to_dict()
                )
            else:
                candidates.append(result.model_dump(mode="json"))

        if candidates:
            for extra in extras:
                candidates.extend(c.model_dump(mode="json") for c in extra)
            return {
                "status": "in_progress",
                "candidates": candidates,
                "errors": errors,
                "loop_plan": loop_plan,
            }
        if no_route_only:
            return {"status": "no_route", "errors": errors}
        return {"status": "provider_failure", "errors": errors}

    return route_with_provider
