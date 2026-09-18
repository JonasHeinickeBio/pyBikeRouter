"""Unit tests for the route_with_provider node (request shaping and error
classification at node level)."""

from bike_routing_agent.errors import ProviderNoRouteError, ProviderUnavailableError
from bike_routing_agent.models import RouteCandidate, RouteMetrics
from bike_routing_agent.nodes.route import build_route_node


class CapturingRouter:
    name = "capturing"

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.last_request = None

    async def route(self, request):
        self.last_request = request
        if self._error is not None:
            raise self._error
        return RouteCandidate(
            provider="capturing",
            provider_profile="cycling-regular",
            geometry_geojson={"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            metrics=RouteMetrics(distance_m=1000, duration_s=200),
        )

    async def health(self) -> dict:
        return {"status": "ok"}


def base_state():
    return {
        "resolved_origin": {"lon": 10.5, "lat": 52.3},
        "resolved_destination": {"lon": 10.6, "lat": 52.4},
        "resolved_via": [{"lon": 10.55, "lat": 52.35}],
        "constraints": {"bike_type": "road", "target_distance_km": 20},
    }


async def test_state_is_shaped_into_neutral_routing_request():
    router = CapturingRouter()
    node = build_route_node(routing_provider=router)

    update = await node(base_state())

    request = router.last_request
    assert request.origin.lon == 10.5
    assert request.via[0].lat == 52.35
    assert request.constraints.bike_type.value == "road"
    assert request.constraints.target_distance_km == 20
    assert update["status"] == "in_progress"
    assert update["candidates"][0]["provider"] == "capturing"


async def test_no_route_error_maps_to_no_route_status():
    router = CapturingRouter(error=ProviderNoRouteError("no path", provider="capturing"))
    node = build_route_node(routing_provider=router)

    update = await node(base_state())

    assert update["status"] == "no_route"
    assert update["errors"][0]["code"] == "no_route"


async def test_generic_provider_error_maps_to_provider_failure():
    router = CapturingRouter(
        error=ProviderUnavailableError("down for maintenance", provider="capturing")
    )
    node = build_route_node(routing_provider=router)

    update = await node(base_state())

    assert update["status"] == "provider_failure"
    assert update["errors"][0]["code"] == "provider_unavailable"
    assert update["errors"][0]["message"] == "down for maintenance"


async def test_empty_via_list_is_accepted():
    router = CapturingRouter()
    node = build_route_node(routing_provider=router)

    state = base_state()
    state["resolved_via"] = []
    await node(state)

    assert router.last_request.via == []
