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

    # Issue #6: single-engine mode still exposes the candidate list -- the
    # selected route, repeated, with raw provider payloads stripped.
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["provider"] == "ors"
    assert body["candidates"][0]["raw_provider_response"] is None
    assert body["route"] == body["candidates"][0]


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


# ------------------------------------------------- candidates (issue #6)


class _StubGraph:
    """Stands in for the compiled LangGraph: returns a fixed final state."""

    def __init__(self, final_state: dict) -> None:
        self._final_state = final_state

    async def ainvoke(self, _initial_state: dict) -> dict:
        return self._final_state


def _candidate_dict(provider: str, score: float | None, geometry: dict) -> dict:
    return {
        "provider": provider,
        "provider_profile": f"{provider}-profile",
        "geometry_geojson": geometry,
        "metrics": {"distance_m": 1000.0, "duration_s": 600.0},
        "score": score,
        "score_breakdown": {},
        "warnings": [],
        "provenance": {},
        "raw_provider_response": {"internal": "must-not-leak"},
    }


def _line(coords: list[tuple[float, float]]) -> dict:
    return {"type": "LineString", "coordinates": [list(c) for c in coords]}


def _ready_state(entries: list[tuple[str, float | None]]) -> dict:
    """A ready final state where each provider scores its own line."""
    candidates = [
        _candidate_dict(provider, score, _line([(0.0, float(i)), (1.0, float(i))]))
        for i, (provider, score) in enumerate(entries)
    ]
    best = max(
        candidates,
        key=lambda c: c["score"] if c["score"] is not None else float("-inf"),
    )
    return {
        "status": "ready",
        "selected_candidate": best,
        "candidates": candidates,
        "explanation": "ok",
        "artifacts": {},
        "errors": [],
    }


def _best_first(entries: list[tuple[str, float | None]]) -> list[str]:
    return [
        provider
        for provider, _ in sorted(
            entries,
            key=lambda entry: entry[1] if entry[1] is not None else float("-inf"),
            reverse=True,
        )
    ]


@pytest.mark.parametrize(
    "entries",
    [
        [("ors", 0.9), ("brouter", 0.7), ("valhalla", 0.5)],
        # Input order deliberately shuffled: sorting must not depend on it.
        [("valhalla", 0.8), ("ors", 0.9), ("brouter", 0.6)],
        # A null-scored candidate (scorer could not score it) sorts last.
        [("ors", 0.9), ("brouter", None), ("valhalla", 0.7)],
    ],
)
async def test_plan_route_candidates_sorted_best_first(
    client, monkeypatch, entries
) -> None:
    import bike_routing_agent.api as api_module

    monkeypatch.setattr(api_module, "_graph", _StubGraph(_ready_state(entries)))

    response = await client.post(
        "/v1/route/plan",
        json={"origin": "1.0, 2.0", "destination": "3.0, 4.0"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert [c["provider"] for c in body["candidates"]] == _best_first(entries)
    # The selected candidate is repeated verbatim as the first entry.
    assert body["route"] == body["candidates"][0]
    # Raw provider payloads never leave the API.
    assert body["route"]["raw_provider_response"] is None
    for cand in body["candidates"]:
        assert cand["raw_provider_response"] is None


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


# ---------------------------------------------------------------------------
# Route history and artifact store wiring (issue #7)
# ---------------------------------------------------------------------------


@pytest.fixture
def history(monkeypatch):
    from bike_routing_agent import api as api_module
    from bike_routing_agent.storage.history import InMemoryRouteHistory

    store = InMemoryRouteHistory()
    monkeypatch.setattr(api_module, "_history", store)
    return store


async def _plan_ready(client, nominatim_single_response, ors_directions_response):
    respx.get(NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=nominatim_single_response[:1])
    )
    respx.post(ORS_URL).mock(return_value=httpx.Response(200, json=ors_directions_response))
    response = await client.post(
        "/v1/route/plan",
        json={"origin": "Braunschweig Hauptbahnhof", "destination": "Wolfenbuettel"},
    )
    assert response.status_code == 200
    return response.json()


@respx.mock
async def test_ready_plan_is_recorded_and_queryable(
    client, history, ors_directions_response, nominatim_single_response
):
    body = await _plan_ready(client, nominatim_single_response, ors_directions_response)

    # The history id is the artifact id, so a download links back to its plan.
    assert body["plan_id"] is not None
    assert body["artifacts"]["geojson_url"] == f"/v1/routes/{body['plan_id']}.geojson"

    listing = await client.get("/v1/history/plans", params={"provider": "ors"})
    assert listing.status_code == 200
    [summary] = listing.json()
    assert summary["plan_id"] == body["plan_id"]
    assert summary["status"] == "ready"
    assert summary["candidates"][0]["provider"] == "ors"
    assert summary["candidates"][0]["selected"] is True
    assert "geometry_geojson" not in summary["candidates"][0]

    detail = await client.get(f"/v1/history/plans/{body['plan_id']}")
    assert detail.status_code == 200
    record = detail.json()
    assert record["request"]["origin"] == "Braunschweig Hauptbahnhof"
    assert record["artifacts"]["geojson_file"] == f"{body['plan_id']}.geojson"
    stored = record["candidates"][0]["candidate"]
    assert stored["geometry_geojson"]["type"] == "LineString"
    assert stored["raw_provider_response"] is None


@respx.mock
async def test_non_ready_plans_are_recorded_too(client, history, nominatim_ambiguous_response):
    respx.get(NOMINATIM_URL).mock(
        return_value=httpx.Response(200, json=nominatim_ambiguous_response)
    )

    response = await client.post(
        "/v1/route/plan", json={"origin": "Springfield", "destination": "Chicago"}
    )

    body = response.json()
    assert body["status"] == "awaiting_clarification"
    assert body["plan_id"] is not None
    listing = await client.get("/v1/history/plans", params={"status": "awaiting_clarification"})
    assert [p["plan_id"] for p in listing.json()] == [body["plan_id"]]
    assert listing.json()[0]["candidates"] == []


@respx.mock
async def test_history_failure_never_fails_the_plan(
    client, monkeypatch, caplog, ors_directions_response, nominatim_single_response
):
    from bike_routing_agent import api as api_module

    class BrokenHistory:
        def save(self, record):
            raise RuntimeError("database is down")

    monkeypatch.setattr(api_module, "_history", BrokenHistory())

    body = await _plan_ready(client, nominatim_single_response, ors_directions_response)

    assert body["status"] == "ready"
    assert body["plan_id"] is None
    assert "failed to record plan" in caplog.text


@respx.mock
async def test_plan_id_is_null_without_history(
    client, monkeypatch, ors_directions_response, nominatim_single_response
):
    from bike_routing_agent import api as api_module

    monkeypatch.setattr(api_module, "_history", None)

    body = await _plan_ready(client, nominatim_single_response, ors_directions_response)

    assert body["status"] == "ready"
    assert body["plan_id"] is None


@pytest.mark.parametrize("path", ["/v1/history/plans", f"/v1/history/plans/{'a' * 32}"])
async def test_history_endpoints_are_503_without_a_database(client, monkeypatch, path):
    from bike_routing_agent import api as api_module

    monkeypatch.setattr(api_module, "_history", None)

    response = await client.get(path)

    assert response.status_code == 503
    assert "DATABASE_URL" in response.json()["detail"]


async def test_history_unknown_or_malformed_plan_id_is_404(client, history):
    assert (await client.get(f"/v1/history/plans/{'a' * 32}")).status_code == 404
    assert (await client.get("/v1/history/plans/not-an-id")).status_code == 404


@pytest.mark.parametrize(
    "params",
    [
        {"bbox": "1,2,3"},
        {"bbox": "a,b,c,d"},
        {"bbox": "5,0,1,1"},
        {"bbox": "0,0,1,95"},
        {"limit": 0},
        {"limit": 1000},
        {"offset": -1},
        {"status": "bogus"},
    ],
)
async def test_history_query_validation(client, history, params):
    response = await client.get("/v1/history/plans", params=params)

    assert response.status_code == 422


async def test_history_query_passes_every_filter_through(client, history):
    seen = {}
    history.query = lambda f: seen.setdefault("filter", f) and []

    response = await client.get(
        "/v1/history/plans",
        params={
            "provider": "ors",
            "profile": "cycling-road",
            "status": "ready",
            "bike_type": "road",
            "selected_only": "true",
            "since": "2026-09-01T00:00:00Z",
            "until": "2026-10-01T00:00:00Z",
            "bbox": "10,52,11,53",
            "limit": 5,
            "offset": 10,
        },
    )

    assert response.status_code == 200
    f = seen["filter"]
    assert (f.provider, f.profile) == ("ors", "cycling-road")
    assert (f.status, f.bike_type) == ("ready", "road")
    assert f.selected_only is True
    assert f.bbox == (10.0, 52.0, 11.0, 53.0)
    assert (f.limit, f.offset) == (5, 10)
    assert f.since is not None and f.since.tzinfo is not None and f.until is not None


async def test_artifacts_are_served_through_the_artifact_store(client, monkeypatch):
    from bike_routing_agent import api as api_module

    class DictStore:
        data = {"a" * 32 + ".geojson": b"{}", "a" * 32 + ".gpx": b"<gpx/>"}

        def put(self, name, content):
            raise AssertionError("read-only here")

        def get(self, name):
            return self.data.get(name)

    monkeypatch.setattr(api_module, "_artifact_store", DictStore())

    geojson = await client.get(f"/v1/routes/{'a' * 32}.geojson")
    gpx = await client.get(f"/v1/routes/{'a' * 32}.gpx")
    missing = await client.get(f"/v1/routes/{'b' * 32}.geojson")

    assert geojson.status_code == 200
    assert geojson.headers["content-type"].startswith("application/geo+json")
    assert gpx.headers["content-type"].startswith("application/gpx+xml")
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# iPhone / home-screen app (docs/mobile.md)
# ---------------------------------------------------------------------------


async def test_pwa_manifest_is_served_and_references_real_icons(client):
    response = await client.get("/manifest.webmanifest")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    manifest = response.json()
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "./"
    for icon in manifest["icons"]:
        asset = await client.get("/" + icon["src"])
        assert asset.status_code == 200
        assert asset.headers["content-type"] == "image/png"


async def test_index_declares_the_iphone_home_screen_app(client):
    page = (await client.get("/")).text

    assert "viewport-fit=cover" in page
    assert 'name="apple-mobile-web-app-capable"' in page
    assert 'rel="manifest" href="manifest.webmanifest"' in page
    apple_icon = await client.get("/icons/apple-touch-icon.png")
    assert apple_icon.status_code == 200
    assert 'rel="apple-touch-icon" href="icons/apple-touch-icon.png"' in page
