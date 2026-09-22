"""Live loop-route test against the real openrouteservice API (issue #5).

Excluded from the default test run (see tool.pytest.ini_options.addopts).
Run explicitly with `pytest -m live` (ORS_API_KEY required, as in
test_live_ors.py). Exercises the synthesized way-point loop the agent
builds for single-origin requests.
"""

import math

import pytest

from bike_routing_agent.config import settings
from bike_routing_agent.loops import synthesize_loop_vias
from bike_routing_agent.models import Coordinate, RouteConstraints, RoutingRequest
from bike_routing_agent.providers.ors import OpenRouteServiceAdapter

pytestmark = pytest.mark.live

_has_ors_key = bool(settings.ors_api_key) and settings.ors_api_key != "changeme"

_ORIGIN = Coordinate(lon=10.5267, lat=52.2689)  # Braunschweig Hauptbahnhof
_TARGET_M = 12_000


def _close_enough(a: Coordinate, b: Coordinate, tolerance_m: float) -> bool:
    dlon = (a.lon - b.lon) * math.cos(math.radians(a.lat)) * 111_320
    dlat = (a.lat - b.lat) * 110_574
    return math.hypot(dlon, dlat) <= tolerance_m


@pytest.mark.skipif(not _has_ors_key, reason="ORS_API_KEY not set")
async def test_live_ors_loop_returns_to_its_origin():
    plan = synthesize_loop_vias(_ORIGIN, _TARGET_M, direction="clockwise")
    adapter = OpenRouteServiceAdapter(
        api_key=settings.ors_api_key,
        base_url="https://api.openrouteservice.org",
        timeout_s=20.0,
    )
    request = RoutingRequest(
        origin=_ORIGIN,
        destination=_ORIGIN,  # loop: end where we started
        via=list(plan.vias),
        constraints=RouteConstraints(bike_type="gravel"),
    )

    candidate = await adapter.route(request)

    coordinates = candidate.geometry_geojson["coordinates"]
    first = Coordinate(lon=coordinates[0][0], lat=coordinates[0][1])
    last = Coordinate(lon=coordinates[-1][0], lat=coordinates[-1][1])
    assert _close_enough(first, last, tolerance_m=250.0)
    # Roads are never the straight-line circuit; allow a generous corridor.
    assert 0.5 * _TARGET_M < candidate.metrics.distance_m < 2.0 * _TARGET_M
