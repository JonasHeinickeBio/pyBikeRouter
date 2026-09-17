"""Live integration test against the real openrouteservice API.

Excluded from the default test run (see tool.pytest.ini_options.addopts).
Run explicitly with `pytest -m live`, and set ORS_API_KEY first.
"""

import os

import pytest

from bike_routing_agent.models import Coordinate, RouteConstraints, RoutingRequest
from bike_routing_agent.providers.ors import OpenRouteServiceAdapter

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.environ.get("ORS_API_KEY"), reason="ORS_API_KEY not set")
async def test_live_ors_route_between_two_real_points():
    adapter = OpenRouteServiceAdapter(
        api_key=os.environ["ORS_API_KEY"],
        base_url="https://api.openrouteservice.org",
        timeout_s=15.0,
    )
    request = RoutingRequest(
        origin=Coordinate(lon=10.5267, lat=52.2689),
        destination=Coordinate(lon=10.5450, lat=52.2201),
        constraints=RouteConstraints(bike_type="gravel"),
    )

    candidate = await adapter.route(request)

    assert candidate.metrics.distance_m > 0
