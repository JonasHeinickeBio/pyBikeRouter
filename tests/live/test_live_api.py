"""Live end-to-end test of the running API against real OSM Nominatim and
real openrouteservice -- no mocks anywhere in this file.

Excluded from the default test run (see tool.pytest.ini_options.addopts).
Run explicitly with `pytest -m live`. Routing assertions adapt to whether
an ORS key is configured: without one, ORS is still reached for real and
must turn its rejection into a structured `provider_failure`, not a crash.

Checked via bike_routing_agent.config.settings rather than os.environ:
the app loads ORS_API_KEY through pydantic-settings' .env support, which
is a different source than the shell environment -- a key present only in
.env would otherwise make the app succeed while this file's branching
still assumed failure.
"""

import shutil

import httpx
import pytest

from bike_routing_agent.api import _export_dir, app
from bike_routing_agent.config import settings

pytestmark = pytest.mark.live

_has_ors_key = bool(settings.ors_api_key) and settings.ors_api_key != "changeme"


@pytest.fixture(autouse=True)
def _clean_export_dir():
    yield
    shutil.rmtree(_export_dir, ignore_errors=True)


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_live_healthz(client):
    response = await client.get("/healthz")
    assert response.status_code == 200


async def test_live_ambiguous_place_name_returns_real_nominatim_candidates(client):
    response = await client.post(
        "/v1/route/plan",
        json={"origin": "Braunschweig Hauptbahnhof", "destination": "Wolfenbuettel"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] in {"awaiting_clarification", "provider_failure", "ready"}
    if body["status"] == "awaiting_clarification":
        assert body["clarification"][0]["candidates"]


async def test_live_route_plan_with_real_coordinates(client):
    response = await client.post(
        "/v1/route/plan",
        json={
            "origin": {"lon": 10.5267, "lat": 52.2689},
            "destination": {"lon": 10.5450, "lat": 52.2201},
            "constraints": {"bike_type": "gravel", "max_ascent_m": 500},
        },
    )

    body = response.json()
    assert response.status_code == 200

    if _has_ors_key:
        assert body["status"] == "ready"
        assert body["route"]["metrics"]["distance_m"] > 0
        assert set(body["artifacts"]) == {"geojson_url", "gpx_url"}
    else:
        assert body["status"] == "provider_failure"
        assert body["errors"][0]["provider"] == "ors"
