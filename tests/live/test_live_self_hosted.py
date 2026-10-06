"""Live smoke test for the self-hosted stack (docs/self-hosted.md, issue #26).

Needs the `self-hosted` compose profile running with the default Bremen
extract (scripts/self-hosted-bootstrap.sh). Skipped unless both URLs are set:

    SELF_HOSTED_ORS_URL=http://127.0.0.1:8080/ors \\
    SELF_HOSTED_GEOCODER_URL=http://127.0.0.1:8081 \\
    poetry run pytest -m live tests/live/test_live_self_hosted.py
"""

import os

import pytest

from bike_routing_agent.models import Coordinate, RouteConstraints, RoutingRequest
from bike_routing_agent.providers.geocoder import NominatimGeocoder
from bike_routing_agent.providers.ors import OpenRouteServiceAdapter

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("SELF_HOSTED_ORS_URL") and os.environ.get("SELF_HOSTED_GEOCODER_URL")),
        reason="SELF_HOSTED_ORS_URL / SELF_HOSTED_GEOCODER_URL not set",
    ),
]

HAUPTBAHNHOF = Coordinate(lon=8.8139, lat=53.0834)
BUERGERPARK = Coordinate(lon=8.8394, lat=53.0953)


@pytest.fixture
def ors():
    return OpenRouteServiceAdapter(
        api_key="", base_url=os.environ["SELF_HOSTED_ORS_URL"], timeout_s=30.0
    )


async def test_local_ors_is_healthy_and_routes_with_elevation(ors):
    assert (await ors.health())["status"] == "ok"
    candidate = await ors.route(
        RoutingRequest(
            origin=HAUPTBAHNHOF,
            destination=BUERGERPARK,
            constraints=RouteConstraints(bike_type="gravel"),
        )
    )
    assert 2_000 < candidate.metrics.distance_m < 4_500
    # An all-zero profile means the elevation download failed on first start
    # (docs/self-hosted.md, troubleshooting): ascent over a few km of Bremen
    # is small but not exactly zero.
    assert candidate.metrics.ascent_m and candidate.metrics.ascent_m > 0


async def test_local_nominatim_geocodes_a_known_place():
    geocoder = NominatimGeocoder(
        base_url=os.environ["SELF_HOSTED_GEOCODER_URL"],
        user_agent="bike-routing-agent/tests",
        timeout_s=15.0,
    )
    assert (await geocoder.health())["status"] == "ok"
    candidates = await geocoder.geocode("Bremen Hauptbahnhof")
    assert candidates
    top = candidates[0].coordinate
    assert abs(top.lon - HAUPTBAHNHOF.lon) < 0.02 and abs(top.lat - HAUPTBAHNHOF.lat) < 0.02
