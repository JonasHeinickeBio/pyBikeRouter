"""Live integration test against a local BRouter RouteServer.

Excluded from the default test run (see tool.pytest.ini_options.addopts).
Run explicitly with `pytest -m live` while

    docker compose -f docker/compose.yaml --profile brouter up

is running with at least one .rd5 segment covering the test region
(see docker/brouter/README.md). Skips when the server is not reachable.
"""

import pytest

from bike_routing_agent.config import settings
from bike_routing_agent.models import Coordinate, RouteConstraints, RoutingRequest
from bike_routing_agent.providers.brouter import BRouterAdapter

pytestmark = pytest.mark.live


async def test_live_brouter_route_with_versioned_custom_profile():
    adapter = BRouterAdapter(
        base_url=settings.brouter_base_url,
        timeout_s=settings.brouter_timeout_s,
        max_retries=settings.brouter_max_retries,
    )
    health = await adapter.health()
    if health["status"] != "ok":
        pytest.skip(f"no local BRouter server at {settings.brouter_base_url} ({health})")

    request = RoutingRequest(
        origin=Coordinate(lon=10.5267, lat=52.2689),
        destination=Coordinate(lon=10.5450, lat=52.2201),
        constraints=RouteConstraints(bike_type="touring"),
    )

    candidate = await adapter.route(request)

    assert candidate.provider_profile == "custom_touring-v1"
    assert candidate.metrics.distance_m > 0
    assert candidate.geometry_geojson["type"] == "LineString"
