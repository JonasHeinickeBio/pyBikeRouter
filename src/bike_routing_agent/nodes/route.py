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
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from bike_routing_agent.errors import ProviderError, ProviderNoRouteError
from bike_routing_agent.models import Coordinate, RouteConstraints, RoutingRequest
from bike_routing_agent.providers.base import RoutingProvider
from bike_routing_agent.state import RouteAgentState

NodeFn = Callable[[RouteAgentState], Awaitable[dict[str, Any]]]


def build_route_node(*, routing_providers: Sequence[RoutingProvider]) -> NodeFn:
    if not routing_providers:
        raise ValueError("build_route_node requires at least one routing provider")

    async def route_with_provider(state: RouteAgentState) -> dict[str, Any]:
        request = RoutingRequest(
            origin=Coordinate.model_validate(state["resolved_origin"]),
            destination=Coordinate.model_validate(state["resolved_destination"]),
            via=[Coordinate.model_validate(v) for v in state.get("resolved_via", [])],
            constraints=RouteConstraints.model_validate(state.get("constraints", {})),
        )

        results = await asyncio.gather(
            *(provider.route(request) for provider in routing_providers),
            return_exceptions=True,
        )

        candidates: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        no_route_only = True
        for provider, result in zip(routing_providers, results, strict=True):
            if isinstance(result, BaseException) and not isinstance(result, Exception):
                raise result
            if isinstance(result, ProviderNoRouteError):
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
            return {"status": "in_progress", "candidates": candidates, "errors": errors}
        if no_route_only:
            return {"status": "no_route", "errors": errors}
        return {"status": "provider_failure", "errors": errors}

    return route_with_provider
