"""route_with_ors node.

Builds an engine-neutral RoutingRequest from resolved coordinates and calls
the routing provider. Provider failures become structured state, never
uncaught exceptions -- `no_route` (no path exists) is distinguished from
`provider_failure` (timeout, rate limit, bad response) so callers can react
differently.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from bike_routing_agent.errors import ProviderError, ProviderNoRouteError
from bike_routing_agent.models import Coordinate, RouteConstraints, RoutingRequest
from bike_routing_agent.providers.base import RoutingProvider
from bike_routing_agent.state import RouteAgentState

NodeFn = Callable[[RouteAgentState], Awaitable[dict[str, Any]]]


def build_route_node(*, routing_provider: RoutingProvider) -> NodeFn:
    async def route_with_provider(state: RouteAgentState) -> dict[str, Any]:
        request = RoutingRequest(
            origin=Coordinate.model_validate(state["resolved_origin"]),
            destination=Coordinate.model_validate(state["resolved_destination"]),
            via=[Coordinate.model_validate(v) for v in state.get("resolved_via", [])],
            constraints=RouteConstraints.model_validate(state.get("constraints", {})),
        )

        try:
            candidate = await routing_provider.route(request)
        except ProviderNoRouteError as exc:
            return {"status": "no_route", "errors": [exc.to_dict()]}
        except ProviderError as exc:
            return {"status": "provider_failure", "errors": [exc.to_dict()]}

        return {
            "status": "in_progress",
            "candidates": [candidate.model_dump(mode="json")],
        }

    return route_with_provider
