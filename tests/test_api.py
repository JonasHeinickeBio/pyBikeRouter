"""Full-stack API tests: real FastAPI app + real graph wiring, with only the
outbound HTTP calls to Nominatim/ORS mocked at the transport layer.

Unlike tests/graph/test_graph.py (which drives the graph directly with fake
providers), these exercise bike_routing_agent.api end to end -- dependency
construction, the compiled graph, and the request/response mapping -- the
same code path a real client hits.
"""

import shutil

import httpx
import pytest
import respx

from bike_routing_agent.api import _export_dir, app

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
ORS_URL = "https://api.openrouteservice.org/v2/directions/cycling-regular/geojson"


@pytest.fixture(autouse=True)
def _clean_export_dir():
    yield
    shutil.rmtree(_export_dir, ignore_errors=True)


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_healthz(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_frontend_index_is_served_at_root(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "pyBikeRouter" in response.text


async def test_frontend_static_assets_are_served(client):
    for path, marker in [("/app.js", "route/plan"), ("/styles.css", "--accent")]:
        response = await client.get(path)
        assert response.status_code == 200
        assert marker in response.text


async def test_plan_route_missing_field_rejected_before_graph_runs(client):
    response = await client.post("/v1/route/plan", json={"origin": "Berlin"})
    assert response.status_code == 422
    # The destination requirement is a model-level contract now (issue #5),
    # so the error speaks about the whole body, not one field.
    assert any("destination is required" in err["msg"] for err in response.json()["detail"])


async def test_plan_route_invalid_constraints_rejected_before_graph_runs(client):
    response = await client.post(
        "/v1/route/plan",
        json={
            "origin": "Berlin",
            "destination": "Hamburg",
            "constraints": {"target_distance_km": -5},
        },
    )
    assert response.status_code == 422


@respx.mock
async def test_plan_route_ready_end_to_end(
    client, ors_directions_response, nominatim_single_response
):
    respx.get(NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=nominatim_single_response[:1])
    )
    respx.post(ORS_URL).mock(return_value=httpx.Response(200, json=ors_directions_response))

    shutil.rmtree(_export_dir, ignore_errors=True)
    response = await client.post(
        "/v1/route/plan",
        json={
            "origin": "Braunschweig Hauptbahnhof",
            "destination": "Wolfenbuettel",
            "constraints": {"bike_type": "gravel", "max_ascent_m": 500},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["route"]["provider"] == "ors"
    assert body["route"]["raw_provider_response"] is None
    assert body["explanation"]
    assert set(body["artifacts"]) == {"geojson_url", "gpx_url"}

    geojson_resp = await client.get(body["artifacts"]["geojson_url"])
    assert geojson_resp.status_code == 200
    assert geojson_resp.json()["type"] == "Feature"

    gpx_resp = await client.get(body["artifacts"]["gpx_url"])
    assert gpx_resp.status_code == 200
    assert b"<gpx" in gpx_resp.content


@respx.mock
async def test_plan_route_ambiguous_geocoding_returns_clarification(
    client, nominatim_ambiguous_response
):
    respx.get(NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=nominatim_ambiguous_response)
    )

    response = await client.post(
        "/v1/route/plan", json={"origin": "Springfield", "destination": "Chicago"}
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "awaiting_clarification"
    assert len(body["clarification"][0]["candidates"]) == len(nominatim_ambiguous_response)
    assert body["route"] is None


@respx.mock
async def test_plan_route_ors_failure_becomes_structured_error(client, nominatim_single_response):
    respx.get(NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=nominatim_single_response[:1])
    )
    respx.post(ORS_URL).mock(
        return_value=httpx.Response(401, json={"error": "Authorization field missing"})
    )

    response = await client.post(
        "/v1/route/plan",
        json={"origin": "Braunschweig Hauptbahnhof", "destination": "Wolfenbuettel"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "provider_failure"
    assert body["errors"][0]["provider"] == "ors"


async def test_get_unknown_artifact_returns_404(client):
    response = await client.get("/v1/routes/deadbeefdeadbeefdeadbeefdeadbeef.geojson")
    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/v1/routes/nope.geojson",
        "/v1/routes/deadbeef.geojson",
        "/v1/routes/deadbeefdeadbeefdeadbeefdeadbeee.geojson",
        "/v1/routes/deadbeefdeadbeefdeadbeefdeadbeef.exe",
        "/v1/routes/deadbeefdeadbeefdeadbeefdeadbeef.geojson.txt",
    ],
)
async def test_artifact_route_rejects_anything_off_the_safe_filename_pattern(client, path):
    response = await client.get(path)
    assert response.status_code == 404


async def test_artifact_route_rejects_path_traversal(client):
    response = await client.get("/v1/routes/..%2F..%2F..%2Fetc%2Fpasswd.geojson")
    assert response.status_code == 404


# ---------------------------------------------------------------- loops (issue #5)


async def test_loop_with_destination_is_rejected(client):
    response = await client.post(
        "/v1/route/plan",
        json={
            "origin": "Berlin",
            "destination": "Potsdam",
            "constraints": {"return_to_origin": True, "target_distance_km": 15},
        },
    )
    assert response.status_code == 422
    assert any("destination must be omitted" in err["msg"] for err in response.json()["detail"])


async def test_loop_without_target_distance_is_rejected(client):
    response = await client.post(
        "/v1/route/plan",
        json={"origin": "Berlin", "constraints": {"return_to_origin": True}},
    )
    assert response.status_code == 422
    assert any("target_distance_km" in err["msg"] for err in response.json()["detail"])


@respx.mock
async def test_plan_route_loop_ready_end_to_end(
    client, ors_directions_response, nominatim_single_response
):
    respx.get(NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=nominatim_single_response[:1])
    )
    respx.post(ORS_URL).mock(return_value=httpx.Response(200, json=ors_directions_response))

    shutil.rmtree(_export_dir, ignore_errors=True)
    response = await client.post(
        "/v1/route/plan",
        json={
            "origin": "Braunschweig Hauptbahnhof",
            "constraints": {"return_to_origin": True, "target_distance_km": 15},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert "loop back to the start" in body["explanation"]
