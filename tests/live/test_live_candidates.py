"""Live real-world tests for the multi-engine candidate contract (issue #6):
the API must expose every scored provider in ``candidates``, sorted best
first, with ``route`` repeating entry 0 and raw payloads stripped.

Excluded from the default test run (see tool.pytest.ini_options.addopts).
Run explicitly with:

    pytest -m live tests/live/test_live_candidates.py

Requirements:
- ORS_API_KEY (shell env or .env), as in test_live_ors.py.
- The multi-engine test needs a local BRouter instance (``docker/brouter/``);
  it skips automatically when none answers on ``BROUTER_BASE_URL``.

Place text is deliberately not used here -- Nominatim behaviour (ambiguity,
clarification) is already exercised live by test_live_api.py. Raw
coordinates keep the focus on what this file owns: the real ORS and BRouter
engines answering in parallel and their candidates coming back comparable.
"""

import shutil

import httpx
import pytest

import bike_routing_agent.api as api_module
from bike_routing_agent.api import _export_dir, app, build_graph_for_settings
from bike_routing_agent.config import Settings, settings

pytestmark = pytest.mark.live

_HAS_ORS_KEY = bool(settings.ors_api_key) and settings.ors_api_key != "changeme"

# Braunschweig Hbf -> Wolfenbuettel, ~15 km -- long enough that the engines
# genuinely disagree on the geometry, short enough for free-tier ORS.
ORIGIN = {"lon": 10.5267, "lat": 52.2689}
DESTINATION = {"lon": 10.7160, "lat": 52.2390}


def _brouter_reachable() -> bool:
    try:
        response = httpx.get(f"{settings.brouter_base_url}/robots.txt", timeout=3.0)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


requires_ors_key = pytest.mark.skipif(not _HAS_ORS_KEY, reason="ORS_API_KEY not configured")


@pytest.fixture(autouse=True)
def _clean_export_dir():
    yield
    shutil.rmtree(_export_dir, ignore_errors=True)


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@requires_ors_key
async def test_live_single_engine_candidates_contract(client):
    """Default configuration (ORS only): candidates is the selected route,
    repeated -- never missing, never raw."""
    response = await client.post(
        "/v1/route/plan",
        json={
            "origin": ORIGIN,
            "destination": DESTINATION,
            "constraints": {"bike_type": "gravel"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"

    candidates = body["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["provider"] == "ors"
    assert candidates[0]["raw_provider_response"] is None
    assert body["route"] == candidates[0]

    # Real geometry from the real engine, not an empty stub.
    assert candidates[0]["geometry_geojson"]["type"] in {"LineString", "MultiLineString"}
    assert candidates[0]["metrics"]["distance_m"] > 0

    # The selected route's artifacts are still exported and served.
    geojson_resp = await client.get(body["artifacts"]["geojson_url"])
    assert geojson_resp.status_code == 200
    assert geojson_resp.json()["type"] == "Feature"


@requires_ors_key
async def test_live_multi_engine_candidates_compare_engines(client, monkeypatch):
    """routing_provider='all': ORS and BRouter answer in parallel, both
    surface in candidates, and a down Valhalla degrades to a recorded error
    instead of sinking the request."""
    if not _brouter_reachable():
        pytest.skip(
            f"no local BRouter server at {settings.brouter_base_url} (see docker/brouter/)"
        )
    cfg = Settings(routing_provider="all")
    monkeypatch.setattr(api_module, "_graph", build_graph_for_settings(cfg))

    response = await client.post(
        "/v1/route/plan",
        json={
            "origin": ORIGIN,
            "destination": DESTINATION,
            "constraints": {"bike_type": "gravel"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"

    candidates = body["candidates"]
    providers = {c["provider"] for c in candidates}
    assert {"ors", "brouter"} <= providers
    assert len(candidates) >= 2

    # Sorted best first; ties are legal, order is not.
    scores = [c["score"] if c["score"] is not None else float("-inf") for c in candidates]
    assert scores == sorted(scores, reverse=True)

    # The selected candidate is entry 0, verbatim.
    assert body["route"] == candidates[0]

    for cand in candidates:
        assert cand["raw_provider_response"] is None
        assert cand["metrics"]["distance_m"] > 0

    # In the real world the engines genuinely disagree: two different
    # cost models, two different geometries.
    geometries = {c["geometry_geojson"]["coordinates"] for c in candidates}
    assert len(geometries) == len(candidates)

    # Partial-failure contract: a missing engine is recorded, not fatal.
    if "valhalla" not in providers:
        assert any(e["provider"] == "valhalla" for e in body["errors"])
